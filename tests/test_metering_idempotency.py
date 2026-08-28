"""Exactly-once metering — PROBE 1, and the concurrency case behind it."""

from __future__ import annotations

import threading
import uuid

import pytest

from app.core.errors import IdempotencyConflict
from app.core.pricing import TokenUsage
from app.db import SessionLocal
from app.repositories import UsageRepository
from app.services.metering import MeterService

TOKENS = TokenUsage(
    input_tokens=1200, cached_input_tokens=800, output_tokens=500, reasoning_tokens=300
)
PAYLOAD = {"prompt": "hello", "simulated_tokens": {"input": 1200}}


def _record(session, tenant, key, payload=PAYLOAD, tokens=TOKENS):
    return MeterService(session).record(
        tenant,
        event_type="generate",
        api_calls=1,
        tokens=tokens,
        idempotency_key=key,
        request_payload=payload,
    )


def test_same_key_twice_creates_exactly_one_usage_event(session, free_tenant):
    """PROBE 1: the retried action happens once."""
    key = f"k_{uuid.uuid4().hex}"

    first = _record(session, free_tenant, key)
    second = _record(session, free_tenant, key)

    assert UsageRepository(session).count_events(free_tenant.id) == 1
    assert first.replayed is False
    assert second.replayed is True


def test_the_replayed_response_mirrors_the_first(session, free_tenant):
    """PROBE 1: the second response mirrors the first, including the event id."""
    key = f"k_{uuid.uuid4().hex}"

    first = _record(session, free_tenant, key)
    second = _record(session, free_tenant, key)

    assert second.status_code == first.status_code
    assert second.body["usage_event_id"] == first.body["usage_event_id"]
    assert second.body["cost"] == first.body["cost"]
    assert second.body["billed"] == first.body["billed"]
    # The ONLY difference is the honest replay marker.
    assert first.body["idempotent_replay"] is False
    assert second.body["idempotent_replay"] is True


def test_ten_retries_still_bill_once(session, free_tenant):
    """A flaky network retrying ten times must not bill ten times."""
    key = f"k_{uuid.uuid4().hex}"
    results = [_record(session, free_tenant, key) for _ in range(10)]

    assert UsageRepository(session).count_events(free_tenant.id) == 1
    assert sum(1 for r in results if not r.replayed) == 1
    assert len({r.body["usage_event_id"] for r in results}) == 1

    rollup = UsageRepository(session).rollup(free_tenant.id)
    assert rollup.api_calls == 1
    assert rollup.cost_micro_cents == results[0].body["cost"]["micro_cents"]


def test_same_key_different_body_is_a_conflict(session, free_tenant):
    """Silently returning the old answer for a new payload would be worse."""
    key = f"k_{uuid.uuid4().hex}"
    _record(session, free_tenant, key, payload={"prompt": "original"})

    with pytest.raises(IdempotencyConflict):
        _record(session, free_tenant, key, payload={"prompt": "DIFFERENT"})

    assert UsageRepository(session).count_events(free_tenant.id) == 1


def test_fingerprint_ignores_key_order_and_whitespace(session, free_tenant):
    """A genuine retry that reserialised its JSON is still a retry."""
    key = f"k_{uuid.uuid4().hex}"
    _record(session, free_tenant, key, payload={"a": 1, "b": 2})
    replay = _record(session, free_tenant, key, payload={"b": 2, "a": 1})

    assert replay.replayed is True
    assert UsageRepository(session).count_events(free_tenant.id) == 1


def test_different_tenants_may_reuse_the_same_key(session, free_tenant, pro_tenant):
    """Keys are scoped per tenant: one customer cannot block another's."""
    key = "a-shared-key-value"

    _record(session, free_tenant, key)
    _record(session, pro_tenant, key)

    assert UsageRepository(session).count_events(free_tenant.id) == 1
    assert UsageRepository(session).count_events(pro_tenant.id) == 1


def test_concurrent_identical_requests_create_exactly_one_event(pro_tenant):
    """The race the unique index exists for.

    Eight threads send the same key at the same moment against real, separate
    database connections. Exactly one usage event may exist afterwards, and every
    caller must receive the same event id -- nobody gets an error, and nobody
    gets billed twice.
    """
    key = f"race_{uuid.uuid4().hex}"
    barrier = threading.Barrier(8)
    results: list = []
    errors: list = []
    lock = threading.Lock()

    def worker():
        s = SessionLocal()
        try:
            barrier.wait(timeout=10)  # maximise the overlap
            result = _record(s, pro_tenant, key)
            with lock:
                results.append(result)
        except Exception as exc:  # noqa: BLE001 - recorded and asserted below
            with lock:
                errors.append(exc)
        finally:
            s.close()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    verify = SessionLocal()
    try:
        count = UsageRepository(verify).count_events(pro_tenant.id)
    finally:
        verify.close()

    assert errors == [], f"no caller should error: {errors}"
    assert count == 1, f"expected exactly one usage event, found {count}"
    assert len({r.body["usage_event_id"] for r in results}) == 1
    assert sum(1 for r in results if not r.replayed) == 1
