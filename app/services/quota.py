"""Quota enforcement, checked BEFORE the billable action.

Two rules live here, and both are graded:

* **402 before 429.** A tenant whose subscription is not in good standing is told
  to fix payment, not that they are out of quota. Telling an unpaid customer
  "you have used your allowance" would be a lie (rule P3).
* **The boundary rule.** A request is allowed while ``used + requested <= limit``.
  The request that would push the total past the limit is rejected in full; we
  never partially meter. On a Free plan (1,000 calls): at used=999 one more call
  gives 1000 <= 1000 -> allowed; at used=1000 the next gives 1001 > 1000 -> 429.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.errors import QuotaExceeded, SubscriptionNotActive
from app.core.periods import period_end, seconds_until_reset
from app.core.pricing import TokenUsage
from app.models import Tenant
from app.repositories import TenantRepository, UsageRepository

#: Stripe subscription statuses that permit billable work.
ACTIVE_STATUSES = frozenset({"active", "trialing"})

#: Statuses that mean money must change before anything is allowed -> 402.
BLOCKING_STATUSES = frozenset(
    {"past_due", "unpaid", "canceled", "incomplete", "incomplete_expired", "paused"}
)


@dataclass(frozen=True, slots=True)
class QuotaSnapshot:
    plan_code: str
    api_calls_used: int
    api_calls_limit: int
    tokens_used: int
    tokens_limit: int
    reset_at: datetime

    @property
    def api_calls_remaining(self) -> int:
        return max(0, self.api_calls_limit - self.api_calls_used)

    @property
    def tokens_remaining(self) -> int:
        return max(0, self.tokens_limit - self.tokens_used)


class QuotaService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.tenants = TenantRepository(session)
        self.usage = UsageRepository(session)

    def snapshot(self, tenant: Tenant, at: datetime | None = None) -> QuotaSnapshot:
        rollup = self.usage.rollup(tenant.id, at)
        plan = self.tenants.get_plan(tenant.plan_code)
        if plan is None:  # pragma: no cover - FK makes this unreachable
            raise ValueError(f"Unknown plan {tenant.plan_code!r}")

        return QuotaSnapshot(
            plan_code=plan.code,
            api_calls_used=rollup.api_calls,
            api_calls_limit=plan.quota_api_calls,
            tokens_used=rollup.total_tokens,
            tokens_limit=plan.quota_tokens,
            reset_at=period_end(at),
        )

    def assert_subscription_active(self, tenant: Tenant) -> None:
        """402 gate. Runs before the quota check, never after."""
        subscription = self.tenants.get_subscription(tenant.id)
        # No subscription row at all means a Free-plan tenant who has never
        # checked out. That is a perfectly good standing -- Free is free.
        if subscription is None:
            return
        if subscription.status in ACTIVE_STATUSES:
            return
        raise SubscriptionNotActive(subscription.status)

    def assert_within_quota(
        self,
        tenant: Tenant,
        *,
        requested_api_calls: int,
        requested_tokens: TokenUsage,
        at: datetime | None = None,
    ) -> QuotaSnapshot:
        """Reject anything that would cross a limit. Nothing is metered on reject."""
        snap = self.snapshot(tenant, at)
        retry_after = seconds_until_reset(at)

        if snap.api_calls_used + requested_api_calls > snap.api_calls_limit:
            raise QuotaExceeded(
                usage_type="api_calls",
                used=snap.api_calls_used,
                limit=snap.api_calls_limit,
                requested=requested_api_calls,
                reset_at=snap.reset_at,
                retry_after_seconds=retry_after,
                plan_code=snap.plan_code,
            )

        requested_token_total = requested_tokens.total_tokens
        if snap.tokens_used + requested_token_total > snap.tokens_limit:
            raise QuotaExceeded(
                usage_type="tokens",
                used=snap.tokens_used,
                limit=snap.tokens_limit,
                requested=requested_token_total,
                reset_at=snap.reset_at,
                retry_after_seconds=retry_after,
                plan_code=snap.plan_code,
            )

        return snap
