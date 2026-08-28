"""Stripe webhooks — PROBE 3 and PROBE 4.

These tests sign payloads with a **local test secret** using the same HMAC-SHA256
scheme Stripe uses, so signature verification, the tolerance window, replay
deduplication and the Free -> Pro flip are all proven without any network call or
any real Stripe key. Feeding the app a genuinely-signed request and a
genuinely-forged one is a stronger test than mocking the verifier away.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid

import pytest

from app.core.config import get_settings
from app.models import Subscription, Tenant
from app.repositories.tenants import hash_api_key

TEST_WHSEC = "whsec_test_local_signing_secret_for_tests_only"


@pytest.fixture(autouse=True)
def _configure_stripe_secret(monkeypatch):
    """Point the app at a local signing secret for the duration of each test."""
    get_settings.cache_clear()
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", TEST_WHSEC)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy_not_a_real_key")
    yield
    get_settings.cache_clear()


def sign(payload: bytes, secret: str = TEST_WHSEC, timestamp: int | None = None) -> str:
    """Build a Stripe-Signature header exactly as Stripe documents it.

    signed_payload = f"{timestamp}.{raw_body}", HMAC-SHA256 keyed by the secret.
    """
    ts = timestamp if timestamp is not None else int(time.time())
    signed_payload = f"{ts}.".encode() + payload
    signature = hmac.new(
        secret.encode(), signed_payload, hashlib.sha256
    ).hexdigest()
    return f"t={ts},v1={signature}"


def event(event_type: str, obj: dict, event_id: str | None = None) -> dict:
    return {
        "id": event_id or f"evt_{uuid.uuid4().hex}",
        "object": "event",
        "type": event_type,
        "created": int(time.time()),
        "data": {"object": obj},
    }


def post(client, evt: dict, *, secret: str = TEST_WHSEC, timestamp: int | None = None):
    body = json.dumps(evt).encode()
    return client.post(
        "/webhooks/stripe",
        content=body,
        headers={
            "Stripe-Signature": sign(body, secret, timestamp),
            "Content-Type": "application/json",
        },
    )


@pytest.fixture
def checkout_tenant(session):
    tenant_id = f"tnt_wh_{uuid.uuid4().hex[:8]}"
    session.add(
        Tenant(
            id=tenant_id,
            name="Webhook Co",
            api_key_hash=hash_api_key(f"wh_key_{uuid.uuid4().hex}"),
            plan_code="free",
        )
    )
    session.commit()
    return tenant_id


# --- PROBE 4: forged signatures -------------------------------------------


def test_forged_signature_is_rejected_with_400(client, checkout_tenant, session):
    """A signature made with the wrong secret must not be trusted."""
    evt = event(
        "checkout.session.completed",
        {
            "id": "cs_forged",
            "client_reference_id": checkout_tenant,
            "metadata": {"tenant_id": checkout_tenant, "plan_code": "pro"},
            "customer": "cus_forged",
            "subscription": "sub_forged",
        },
    )
    response = post(client, evt, secret="whsec_attacker_guessed_this")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_signature"

    # ...and NOTHING changed.
    session.expire_all()
    assert session.get(Tenant, checkout_tenant).plan_code == "free"


def test_forged_webhook_writes_nothing_at_all(client, session):
    """Not just the plan: no event is recorded either."""
    from app.models import ProcessedWebhookEvent

    before = session.query(ProcessedWebhookEvent).count()
    evt = event("customer.subscription.updated", {"id": "sub_x", "status": "active"})
    assert post(client, evt, secret="whsec_wrong").status_code == 400
    session.expire_all()
    assert session.query(ProcessedWebhookEvent).count() == before


def test_missing_signature_header_is_400(client):
    body = json.dumps(event("customer.subscription.updated", {"id": "s"})).encode()
    response = client.post("/webhooks/stripe", content=body)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "missing_signature"


def test_a_stale_but_validly_signed_event_is_rejected(client):
    """Replay protection: a captured request cannot be reused indefinitely.

    The signature is real, but the timestamp is an hour old — outside the 300s
    tolerance. This is why the tolerance must never be set to 0.
    """
    evt = event("customer.subscription.updated", {"id": "sub_old", "status": "active"})
    response = post(client, evt, timestamp=int(time.time()) - 3600)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_signature"


def test_tampered_payload_fails_verification(client, checkout_tenant):
    """Signature covers the body: editing one byte after signing invalidates it."""
    evt = event(
        "checkout.session.completed",
        {"id": "cs_1", "client_reference_id": checkout_tenant},
    )
    body = json.dumps(evt).encode()
    header = sign(body)

    tampered = body.replace(b'"cs_1"', b'"cs_2"')
    response = client.post(
        "/webhooks/stripe",
        content=tampered,
        headers={"Stripe-Signature": header, "Content-Type": "application/json"},
    )
    assert response.status_code == 400


# --- PROBE 4: replay / deduplication ---------------------------------------


def test_replaying_a_real_event_processes_it_once(client, checkout_tenant, session):
    """Delivery is at-least-once; processing must be exactly-once."""
    evt = event(
        "checkout.session.completed",
        {
            "id": "cs_replay",
            "client_reference_id": checkout_tenant,
            "metadata": {"tenant_id": checkout_tenant, "plan_code": "pro"},
            "customer": "cus_replay",
            "subscription": "sub_replay",
        },
    )

    first = post(client, evt)
    second = post(client, evt)  # identical event id

    assert first.status_code == 200
    assert first.json()["status"] == "processed"
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate_ignored"

    from app.models import ProcessedWebhookEvent

    session.expire_all()
    assert (
        session.query(ProcessedWebhookEvent)
        .filter_by(event_id=evt["id"])
        .count()
        == 1
    )
    # And exactly one subscription row, not two.
    assert session.query(Subscription).filter_by(tenant_id=checkout_tenant).count() == 1


def test_deduplication_keys_on_event_id_not_payload(client, checkout_tenant):
    """Two DIFFERENT events with the same payload are both real events.

    Stripe warns that distinct events can share a timestamp and that ordering is
    not guaranteed, so the event id is the only safe dedup key.
    """
    obj = {
        "id": "cs_same_payload",
        "client_reference_id": checkout_tenant,
        "metadata": {"tenant_id": checkout_tenant, "plan_code": "pro"},
        "customer": "cus_a",
        "subscription": "sub_a",
    }
    first = post(client, event("checkout.session.completed", obj, "evt_one"))
    second = post(client, event("checkout.session.completed", obj, "evt_two"))

    assert first.json()["status"] == "processed"
    assert second.json()["status"] == "processed"  # different id => not a duplicate


# --- PROBE 3: the Free -> Pro flip -----------------------------------------


def test_checkout_completed_flips_the_tenant_from_free_to_pro(
    client, checkout_tenant, session
):
    """PROBE 3, minus the browser: the webhook is what changes the plan."""
    assert session.get(Tenant, checkout_tenant).plan_code == "free"

    response = post(
        client,
        event(
            "checkout.session.completed",
            {
                "id": "cs_upgrade",
                "client_reference_id": checkout_tenant,
                "metadata": {"tenant_id": checkout_tenant, "plan_code": "pro"},
                "customer": "cus_upgrade",
                "subscription": "sub_upgrade",
            },
        ),
    )

    assert response.status_code == 200
    assert response.json()["action"] == "plan_upgraded"

    session.expire_all()
    tenant = session.get(Tenant, checkout_tenant)
    assert tenant.plan_code == "pro"

    subscription = session.query(Subscription).filter_by(tenant_id=checkout_tenant).one()
    assert subscription.status == "active"
    assert subscription.stripe_subscription_id == "sub_upgrade"


def test_usage_endpoint_shows_the_new_limits_after_the_upgrade(
    client, session, checkout_tenant
):
    """PROBE 3 end state: GET /usage reflects the Pro quota."""
    api_key = f"upgrade_key_{uuid.uuid4().hex[:10]}"
    tenant = session.get(Tenant, checkout_tenant)
    tenant.api_key_hash = hash_api_key(api_key)
    session.commit()

    headers = {"Authorization": f"Bearer {api_key}"}
    before = client.get("/v1/usage", headers=headers).json()
    assert before["plan"]["code"] == "free"
    assert before["tokens"]["limit"] == 100_000

    post(
        client,
        event(
            "checkout.session.completed",
            {
                "id": "cs_limits",
                "client_reference_id": checkout_tenant,
                "metadata": {"tenant_id": checkout_tenant, "plan_code": "pro"},
                "customer": "cus_limits",
                "subscription": "sub_limits",
            },
        ),
    )

    after = client.get("/v1/usage", headers=headers).json()
    assert after["plan"]["code"] == "pro"
    assert after["tokens"]["limit"] == 5_000_000
    assert after["api_calls"]["limit"] == 50_000


def test_subscription_deleted_downgrades_to_free(client, checkout_tenant, session):
    post(
        client,
        event(
            "checkout.session.completed",
            {
                "id": "cs_del",
                "client_reference_id": checkout_tenant,
                "metadata": {"tenant_id": checkout_tenant, "plan_code": "pro"},
                "customer": "cus_del",
                "subscription": "sub_del",
            },
        ),
    )
    session.expire_all()
    assert session.get(Tenant, checkout_tenant).plan_code == "pro"

    post(client, event("customer.subscription.deleted", {"id": "sub_del"}))

    session.expire_all()
    assert session.get(Tenant, checkout_tenant).plan_code == "free"
    subscription = session.query(Subscription).filter_by(tenant_id=checkout_tenant).one()
    assert subscription.status == "canceled"


def test_past_due_update_downgrades_limits_but_keeps_the_subscription(
    client, checkout_tenant, session
):
    post(
        client,
        event(
            "checkout.session.completed",
            {
                "id": "cs_pd",
                "client_reference_id": checkout_tenant,
                "metadata": {"tenant_id": checkout_tenant, "plan_code": "pro"},
                "customer": "cus_pd",
                "subscription": "sub_pd",
            },
        ),
    )

    post(
        client,
        event(
            "customer.subscription.updated", {"id": "sub_pd", "status": "past_due"}
        ),
    )

    session.expire_all()
    subscription = session.query(Subscription).filter_by(tenant_id=checkout_tenant).one()
    assert subscription.status == "past_due"
    assert session.get(Tenant, checkout_tenant).plan_code == "free"


def test_unhandled_event_type_is_a_200_noop(client):
    """Returning an error would make Stripe retry it for three days."""
    response = post(client, event("invoice.payment_succeeded", {"id": "in_1"}))
    assert response.status_code == 200
    assert response.json()["status"] == "ignored_unhandled_type"


def test_handlers_are_order_independent(client, checkout_tenant, session):
    """subscription.updated arriving BEFORE checkout.completed must not crash.

    Stripe does not guarantee ordering, so an out-of-order delivery has to be
    survivable rather than fatal.
    """
    early = post(
        client,
        event("customer.subscription.updated", {"id": "sub_orphan", "status": "active"}),
    )
    assert early.status_code == 200
    assert early.json()["action"] == "skipped_unknown_subscription"

    later = post(
        client,
        event(
            "checkout.session.completed",
            {
                "id": "cs_ordering",
                "client_reference_id": checkout_tenant,
                "metadata": {"tenant_id": checkout_tenant, "plan_code": "pro"},
                "customer": "cus_ordering",
                "subscription": "sub_orphan",
            },
        ),
    )
    assert later.json()["action"] == "plan_upgraded"
    session.expire_all()
    assert session.get(Tenant, checkout_tenant).plan_code == "pro"
