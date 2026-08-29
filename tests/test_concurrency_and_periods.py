"""Regression tests for gaps found by attacking the running service.

Each test here exists because something actually failed, not because it seemed
like a good idea. The over-quota race in particular shipped and was measured
leaking free usage before the metering lock was added.
"""

from __future__ import annotations

import threading
import uuid
from datetime import timedelta

import pytest

from app.core.errors import QuotaExceeded
from app.core.periods import period_end, period_start
from app.core.pricing import TokenUsage
from app.db import SessionLocal
from app.repositories import UsageRepository
from app.services.metering import MeterService
from app.services.quota import QuotaService

FREE_CALL_LIMIT = 1_000


def _meter(session, tenant, *, key=None, tokens=None, calls=1, at=None):
    return MeterService(session).record(
        tenant,
        event_type="generate",
        api_calls=calls,
        tokens=tokens or TokenUsage(),
        idempotency_key=key or f"k_{uuid.uuid4().hex}",
        request_payload={"k": key},
        at=at,
    )


# --- the over-quota race ----------------------------------------------------


def test_concurrent_requests_cannot_exceed_the_quota(free_tenant):
    """Different keys, one call of headroom, fired at once. The limit must hold.

    This is the bug that motivated the metering lock. Idempotency does not help
    here: every request is genuinely distinct, so the only thing standing
    between the tenant and free usage is the quota check -- and a bare
    read-then-write quota check is not atomic.

    Measured before the fix: 12 concurrent requests, 7 accepted, 1006/1000.
    """
    # Fill to exactly one call below the limit, cheaply and sequentially.
    setup = SessionLocal()
    try:
        for _ in range(FREE_CALL_LIMIT - 1):
            _meter(setup, free_tenant)
        assert (
            QuotaService(setup).snapshot(free_tenant).api_calls_used
            == FREE_CALL_LIMIT - 1
        )
    finally:
        setup.close()

    n = 12
    barrier = threading.Barrier(n)
    accepted, rejected, errors = [], [], []
    lock = threading.Lock()

    def worker():
        s = SessionLocal()
        try:
            barrier.wait(timeout=15)
            _meter(s, free_tenant)
            with lock:
                accepted.append(1)
        except QuotaExceeded:
            with lock:
                rejected.append(1)
        except Exception as exc:  # noqa: BLE001 - asserted below
            with lock:
                errors.append(exc)
        finally:
            s.close()

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    verify = SessionLocal()
    try:
        final = QuotaService(verify).snapshot(free_tenant).api_calls_used
    finally:
        verify.close()

    assert errors == [], f"contended requests must not error: {errors}"
    assert final <= FREE_CALL_LIMIT, f"quota exceeded: {final} > {FREE_CALL_LIMIT}"
    assert len(accepted) == 1, f"exactly one request had headroom, {len(accepted)} got in"
    assert len(rejected) == n - 1


def test_a_rejected_request_writes_nothing_at_all(session, free_tenant):
    """A 429 must not leave the lock bump, or any other row, behind."""
    _meter(session, free_tenant, tokens=TokenUsage(input_tokens=100_000))
    before = UsageRepository(session).count_events(free_tenant.id)

    with pytest.raises(QuotaExceeded):
        _meter(session, free_tenant, tokens=TokenUsage(input_tokens=1))

    session.rollback()
    assert UsageRepository(session).count_events(free_tenant.id) == before


# --- idempotency keys and transient failures --------------------------------


def test_a_key_rejected_for_quota_can_succeed_after_an_upgrade(session, free_tenant):
    """A quota rejection must not poison the key permanently.

    Stripe stores the outcome of the first attempt whatever it was, so a replay
    returns the original failure. We deliberately diverge: a 402 or 429 is
    *transient*, and a customer who upgrades and retries the same key should be
    served, not told forever that they were once out of quota. Only successful
    outcomes are recorded against a key.

    Documented in docs/DESIGN.md and docs/REFERENCES.md rule I1.
    """
    key = f"retry_after_upgrade_{uuid.uuid4().hex}"
    _meter(session, free_tenant, tokens=TokenUsage(input_tokens=100_000))

    with pytest.raises(QuotaExceeded):
        _meter(session, free_tenant, key=key, tokens=TokenUsage(input_tokens=10))

    session.rollback()
    free_tenant.plan_code = "pro"  # the customer upgrades
    session.commit()

    result = _meter(session, free_tenant, key=key, tokens=TokenUsage(input_tokens=10))
    assert result.replayed is False
    assert result.body["usage_event_id"]


# --- billing period boundaries ----------------------------------------------


def test_usage_from_a_previous_month_is_not_billed_this_month(session, free_tenant):
    """Quota windows are calendar months in UTC. Last month must not leak in."""
    start = period_start()
    cases = {
        "five days before": start - timedelta(days=5),
        "one second before": start - timedelta(seconds=1),
        "exactly at the start": start,
        "one hour inside": start + timedelta(hours=1),
    }
    for label, when in cases.items():
        _meter(session, free_tenant, key=f"period_{label}", at=when)

    rollup = UsageRepository(session).rollup(free_tenant.id)
    # The window's lower bound is inclusive, the upper bound exclusive, so only
    # the last two events count.
    assert rollup.event_count == 2
    assert rollup.api_calls == 2


def test_the_period_window_is_a_whole_utc_month(session):
    start, end = period_start(), period_end()
    assert start.day == 1
    assert (start.hour, start.minute, start.second, start.microsecond) == (0, 0, 0, 0)
    assert end.day == 1
    assert end > start
    assert str(start.tzinfo) == "UTC"


def test_quota_frees_up_when_the_period_rolls_over(session, free_tenant):
    """Usage recorded in a spent month must not block the next one."""
    last_month = period_start() - timedelta(days=2)
    _meter(
        session,
        free_tenant,
        key="spent_last_month",
        tokens=TokenUsage(input_tokens=100_000),
        at=last_month,
    )

    snapshot = QuotaService(session).snapshot(free_tenant)
    assert snapshot.tokens_used == 0
    assert snapshot.tokens_remaining == 100_000

    result = _meter(session, free_tenant, tokens=TokenUsage(input_tokens=50_000))
    assert result.replayed is False
