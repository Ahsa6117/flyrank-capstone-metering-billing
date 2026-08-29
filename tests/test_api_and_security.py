"""HTTP contract, tenant isolation, secret hygiene, and the background job."""

from __future__ import annotations

import uuid

import pytest

from app.core.config import Settings, get_settings
from app.core.logging import redact
from app.models import Tenant
from app.repositories.tenants import hash_api_key


def _auth(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def _generate(client, key, *, idem=None, body=None):
    # `body if body is not None` rather than `body or ...`: an empty dict is a
    # meaningful test case (missing prompt) and is also falsy.
    payload = body if body is not None else {"prompt": "hi", "simulated_tokens": {"input": 10}}
    return client.post(
        "/v1/generate",
        headers={**_auth(key), "Idempotency-Key": idem or f"k_{uuid.uuid4().hex}"},
        json=payload,
    )


# --- HTTP contract ---------------------------------------------------------


def test_health_reports_the_database_not_just_the_process(client):
    """A probe points at /health, so "ok" has to mean the service can work."""
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["stripe"] in {"configured", "not_configured"}


def test_generate_and_usage_round_trip(client, api_tenant):
    _tenant, key = api_tenant
    response = _generate(
        client,
        key,
        body={
            "prompt": "hello",
            "simulated_tokens": {
                "input": 1200,
                "cached_input": 800,
                "output": 500,
                "reasoning": 300,
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["cost"]["micro_cents"] == 2_396_000
    assert response.headers["Idempotent-Replay"] == "false"

    usage = client.get("/v1/usage", headers=_auth(key)).json()
    assert usage["api_calls"]["used"] == 1
    assert usage["tokens"]["used"] == 2_800
    assert usage["tokens"]["breakdown"]["cached_input_tokens"] == 800
    assert usage["cost"]["micro_cents"] == 2_396_000


def test_replay_over_http_sets_the_header(client, api_tenant):
    _tenant, key = api_tenant
    idem = f"k_{uuid.uuid4().hex}"

    first = _generate(client, key, idem=idem)
    second = _generate(client, key, idem=idem)

    assert first.json()["usage_event_id"] == second.json()["usage_event_id"]
    assert second.headers["Idempotent-Replay"] == "true"
    assert second.json()["idempotent_replay"] is True


def test_missing_idempotency_key_is_400(client, api_tenant):
    _tenant, key = api_tenant
    response = client.post("/v1/generate", headers=_auth(key), json={"prompt": "x"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "idempotency_key_required"


def test_oversized_idempotency_key_is_400(client, api_tenant):
    _tenant, key = api_tenant
    response = _generate(client, key, idem="x" * 256)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "idempotency_key_too_long"


def test_unknown_api_key_is_401(client):
    response = _generate(client, "not-a-real-key")
    assert response.status_code == 401


def test_missing_auth_header_is_401(client):
    response = client.post(
        "/v1/generate", headers={"Idempotency-Key": "k1"}, json={"prompt": "x"}
    )
    assert response.status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {"prompt": ""},                                     # too short
        {"prompt": "x", "simulated_tokens": {"input": -1}},  # negative
        {"prompt": "x", "simulated_tokens": {"input": "many"}},  # wrong type
        {"prompt": "x", "unexpected_field": 1},              # extra key
        {},                                                  # missing prompt
    ],
)
def test_bad_input_is_a_clean_4xx_never_a_500(client, api_tenant, body):
    """Requirement S2: validation at the boundary."""
    _tenant, key = api_tenant
    response = _generate(client, key, body=body)
    assert 400 <= response.status_code < 500
    assert "error" in response.json()


def test_usage_needs_no_idempotency_key(client, api_tenant):
    """GET is idempotent by definition (rule I4)."""
    _tenant, key = api_tenant
    assert client.get("/v1/usage", headers=_auth(key)).status_code == 200


# --- tenant isolation ------------------------------------------------------


def test_one_tenant_never_sees_another_tenants_usage(client, session):
    """Requirement R10: customer data isolated per tenant."""
    keys = {}
    for label in ("a", "b"):
        key = f"iso_{label}_{uuid.uuid4().hex[:10]}"
        session.add(
            Tenant(
                id=f"tnt_iso_{label}_{uuid.uuid4().hex[:6]}",
                name=label,
                api_key_hash=hash_api_key(key),
                plan_code="free",
            )
        )
        keys[label] = key
    session.commit()

    for _ in range(3):
        _generate(client, keys["a"], body={"prompt": "x", "simulated_tokens": {"input": 100}})

    usage_a = client.get("/v1/usage", headers=_auth(keys["a"])).json()
    usage_b = client.get("/v1/usage", headers=_auth(keys["b"])).json()

    assert usage_a["api_calls"]["used"] == 3
    assert usage_b["api_calls"]["used"] == 0
    assert usage_b["cost"]["micro_cents"] == 0


# --- secret hygiene --------------------------------------------------------


def test_live_stripe_key_refuses_to_start():
    """The app must not be able to move real money by accident (rule S1)."""
    with pytest.raises(Exception) as excinfo:
        Settings(stripe_secret_key="sk_live_definitely_not_allowed")
    assert "test mode only" in str(excinfo.value).lower()


def test_test_mode_key_is_accepted():
    assert Settings(stripe_secret_key="sk_test_fine").stripe_secret_key.startswith(
        "sk_test_"
    )


@pytest.mark.parametrize(
    "text",
    [
        "using key sk_test_51AbCdEfGhIjKlMnOp",
        "secret is whsec_abc123DEF456",
        "Authorization: Bearer super-secret-token",
        "STRIPE_SECRET_KEY=sk_test_leaky",
        "password: hunter2",
    ],
)
def test_secrets_are_redacted_before_they_can_be_logged(text):
    """Requirement S6: secrets never reach the logs."""
    scrubbed = redact(text)
    assert "***REDACTED***" in scrubbed
    for leak in ("sk_test_51AbCdEfGhIjKlMnOp", "whsec_abc123DEF456", "hunter2"):
        assert leak not in scrubbed


def test_api_keys_are_stored_only_as_hashes(session, api_tenant):
    tenant, key = api_tenant
    assert tenant.api_key_hash != key
    assert len(tenant.api_key_hash) == 64
    assert tenant.api_key_hash == hash_api_key(key)


def test_billing_endpoints_report_missing_config_rather_than_crashing(
    client, api_tenant, monkeypatch
):
    _tenant, key = api_tenant
    get_settings.cache_clear()
    monkeypatch.setenv("STRIPE_SECRET_KEY", "")
    monkeypatch.setenv("STRIPE_PRICE_ID_PRO", "")
    try:
        response = client.post(
            "/v1/billing/checkout", headers=_auth(key), json={"plan_code": "pro"}
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "stripe_not_configured"
    finally:
        get_settings.cache_clear()


# --- background job (requirement S3) ---------------------------------------


def test_job_emits_alerts_at_80_and_100_percent(client, session):
    from app.jobs.rollup_and_alerts import run_job
    from app.models import UsageAlert

    key = f"alert_{uuid.uuid4().hex[:10]}"
    tenant_id = f"tnt_alert_{uuid.uuid4().hex[:6]}"
    session.add(
        Tenant(
            id=tenant_id, name="Alert Co", api_key_hash=hash_api_key(key), plan_code="free"
        )
    )
    session.commit()

    # Drive the tenant to 100% of the token quota.
    _generate(
        client, key, body={"prompt": "x", "simulated_tokens": {"input": 100_000}}
    )

    result = run_job()
    assert result["status"] == "success"

    alerts = (
        session.query(UsageAlert)
        .filter_by(tenant_id=tenant_id, usage_type="tokens")
        .all()
    )
    assert {a.threshold_percent for a in alerts} == {80, 100}


def test_job_rerun_does_not_duplicate_alerts(client, session):
    """The same idempotency discipline as metering, applied to alerts."""
    from app.jobs.rollup_and_alerts import run_job
    from app.models import UsageAlert

    key = f"dupe_{uuid.uuid4().hex[:10]}"
    tenant_id = f"tnt_dupe_{uuid.uuid4().hex[:6]}"
    session.add(
        Tenant(
            id=tenant_id, name="Dupe Co", api_key_hash=hash_api_key(key), plan_code="free"
        )
    )
    session.commit()

    _generate(client, key, body={"prompt": "x", "simulated_tokens": {"input": 90_000}})

    run_job()
    run_job()
    run_job()

    count = session.query(UsageAlert).filter_by(tenant_id=tenant_id).count()
    assert count == 1  # 80% only, emitted once despite three runs


def test_job_retries_then_raises_a_failure_alert(monkeypatch):
    """Requirement S3: retries + a failure alert, not a silent pass."""
    from app.jobs import rollup_and_alerts
    from app.db import SessionLocal
    from app.models import JobRun

    attempts = {"n": 0}

    def always_fails():
        attempts["n"] += 1
        raise RuntimeError("database is on fire")

    monkeypatch.setattr(rollup_and_alerts, "_do_work", always_fails)

    result = rollup_and_alerts.run_job(sleep=lambda _s: None)

    assert result["status"] == "failed"
    assert result["attempts"] == 3
    assert attempts["n"] == 3  # it really retried
    assert "database is on fire" in result["error"]

    s = SessionLocal()
    try:
        failure = (
            s.query(JobRun)
            .filter_by(status="failed")
            .order_by(JobRun.started_at.desc())
            .first()
        )
        assert failure is not None  # the failure alert was recorded
        assert failure.attempts == 3
    finally:
        s.close()


def test_internal_job_endpoint_requires_a_token(client):
    assert client.post("/internal/jobs/run").status_code == 403
    ok = client.post(
        "/internal/jobs/run", headers={"X-Internal-Token": "test-internal-token"}
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "success"


def test_env_example_placeholders_read_as_unconfigured():
    """Copying .env.example verbatim must not look like a configured Stripe.

    The README's first step is `cp .env.example .env`, so the placeholder values
    have to report as unset — otherwise the app appears configured and fails deep
    inside a Stripe call instead of returning a clear 503.
    """
    placeholder = Settings(
        stripe_secret_key="sk_test_replace_me",
        stripe_webhook_secret="whsec_replace_me",
        stripe_price_id_pro="price_replace_me",
    )
    assert placeholder.stripe_configured is False
    assert placeholder.stripe_api_configured is False
    assert placeholder.stripe_webhooks_configured is False

    real = Settings(
        stripe_secret_key="sk_test_realvalue",
        stripe_webhook_secret="whsec_realvalue",
        stripe_price_id_pro="price_realvalue",
    )
    assert real.stripe_configured is True
