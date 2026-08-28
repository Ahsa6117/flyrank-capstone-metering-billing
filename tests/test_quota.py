"""Quota enforcement — PROBE 2, plus the 402-before-429 rule."""

from __future__ import annotations

import uuid

import pytest

from app.core.errors import QuotaExceeded, SubscriptionNotActive
from app.core.periods import now, period_end
from app.core.pricing import TokenUsage
from app.services.metering import MeterService
from app.services.quota import QuotaService

FREE_TOKEN_LIMIT = 100_000
FREE_CALL_LIMIT = 1_000


def _record(session, tenant, tokens, key=None):
    return MeterService(session).record(
        tenant,
        event_type="generate",
        api_calls=1,
        tokens=tokens,
        idempotency_key=key or f"k_{uuid.uuid4().hex}",
        request_payload={"n": tokens.total_tokens},
    )


def test_request_landing_exactly_on_the_limit_is_allowed(session, free_tenant):
    """PROBE 2: the boundary rule is `used + requested <= limit`, inclusive."""
    result = _record(session, free_tenant, TokenUsage(input_tokens=FREE_TOKEN_LIMIT))

    assert result.replayed is False
    snap = QuotaService(session).snapshot(free_tenant)
    assert snap.tokens_used == FREE_TOKEN_LIMIT
    assert snap.tokens_remaining == 0


def test_one_token_past_the_limit_is_rejected(session, free_tenant):
    """The request after the boundary returns 429 with an explanatory message."""
    _record(session, free_tenant, TokenUsage(input_tokens=FREE_TOKEN_LIMIT))

    with pytest.raises(QuotaExceeded) as excinfo:
        _record(session, free_tenant, TokenUsage(input_tokens=1))

    exc = excinfo.value
    assert exc.usage_type == "tokens"
    assert exc.used == FREE_TOKEN_LIMIT
    assert exc.limit == FREE_TOKEN_LIMIT
    assert exc.requested == 1
    assert exc.plan_code == "free"
    assert "exceeds the free plan limit" in str(exc)


def test_a_request_that_would_cross_the_limit_is_rejected_in_full(
    session, free_tenant
):
    """We never partially meter: an over-large request bills nothing at all."""
    _record(session, free_tenant, TokenUsage(input_tokens=99_000))

    with pytest.raises(QuotaExceeded):
        # 99_000 + 5_000 = 104_000 > 100_000. Not 1_000 of it — none of it.
        _record(session, free_tenant, TokenUsage(input_tokens=5_000))

    assert QuotaService(session).snapshot(free_tenant).tokens_used == 99_000


def test_rejected_requests_are_not_metered(session, free_tenant):
    from app.repositories import UsageRepository

    _record(session, free_tenant, TokenUsage(input_tokens=FREE_TOKEN_LIMIT))
    before = UsageRepository(session).count_events(free_tenant.id)

    for _ in range(3):
        with pytest.raises(QuotaExceeded):
            _record(session, free_tenant, TokenUsage(input_tokens=10))

    assert UsageRepository(session).count_events(free_tenant.id) == before


def test_retry_after_points_at_the_actual_reset_moment(session, free_tenant):
    """Retry-After must be when a retry can succeed, not an arbitrary backoff."""
    _record(session, free_tenant, TokenUsage(input_tokens=FREE_TOKEN_LIMIT))

    with pytest.raises(QuotaExceeded) as excinfo:
        _record(session, free_tenant, TokenUsage(input_tokens=1))

    exc = excinfo.value
    expected = int((period_end() - now()).total_seconds())
    assert exc.reset_at == period_end()
    assert abs(exc.retry_after_seconds - expected) <= 2  # clock tick tolerance


def test_api_call_quota_is_enforced_independently(session, free_tenant):
    """A tenant can exhaust calls while tokens remain, and vice versa."""
    quota = QuotaService(session)

    # Zero-token calls: only the call counter moves.
    for _ in range(3):
        _record(session, free_tenant, TokenUsage())

    snap = quota.snapshot(free_tenant)
    assert snap.api_calls_used == 3
    assert snap.tokens_used == 0

    with pytest.raises(QuotaExceeded) as excinfo:
        quota.assert_within_quota(
            free_tenant,
            requested_api_calls=FREE_CALL_LIMIT,
            requested_tokens=TokenUsage(),
        )
    assert excinfo.value.usage_type == "api_calls"


def test_pro_plan_has_the_documented_larger_limits(session, pro_tenant):
    snap = QuotaService(session).snapshot(pro_tenant)
    assert snap.plan_code == "pro"
    assert snap.api_calls_limit == 50_000
    assert snap.tokens_limit == 5_000_000


def test_past_due_subscription_is_402_even_with_quota_remaining(
    session, past_due_tenant
):
    """402 wins over 429: an unpaid tenant is not told they are out of quota."""
    snap = QuotaService(session).snapshot(past_due_tenant)
    assert snap.tokens_remaining > 0  # quota is NOT the problem

    with pytest.raises(SubscriptionNotActive) as excinfo:
        _record(session, past_due_tenant, TokenUsage(input_tokens=10))

    assert excinfo.value.status == "past_due"


@pytest.mark.parametrize(
    "status,blocked",
    [
        ("active", False),
        ("trialing", False),
        ("past_due", True),
        ("unpaid", True),
        ("canceled", True),
        ("incomplete_expired", True),
    ],
)
def test_subscription_status_gate(session, pro_tenant, status, blocked):
    subscription = pro_tenant.subscription
    subscription.status = status
    session.commit()

    quota = QuotaService(session)
    if blocked:
        with pytest.raises(SubscriptionNotActive):
            quota.assert_subscription_active(pro_tenant)
    else:
        quota.assert_subscription_active(pro_tenant)


def test_free_tenant_without_a_subscription_row_is_in_good_standing(
    session, free_tenant
):
    """Free is free: no subscription is not the same as a lapsed subscription."""
    QuotaService(session).assert_subscription_active(free_tenant)
