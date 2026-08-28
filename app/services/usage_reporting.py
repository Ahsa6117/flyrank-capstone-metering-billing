"""The read path: aggregate usage events into { used, limit, cost }."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.money import money_fields
from app.core.periods import period_end, period_start
from app.models import Tenant
from app.repositories import TenantRepository, UsageRepository


class UsageReportingService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.usage = UsageRepository(session)
        self.tenants = TenantRepository(session)

    def report(self, tenant: Tenant, at: datetime | None = None) -> dict[str, Any]:
        rollup = self.usage.rollup(tenant.id, at)
        plan = self.tenants.get_plan(tenant.plan_code)
        subscription = self.tenants.get_subscription(tenant.id)

        assert plan is not None  # guaranteed by the tenants.plan_code foreign key

        return {
            "tenant_id": tenant.id,
            "plan": {
                "code": plan.code,
                "name": plan.name,
                "quota_api_calls": plan.quota_api_calls,
                "quota_tokens": plan.quota_tokens,
            },
            "subscription_status": subscription.status if subscription else "none",
            "period": {
                "start": period_start(at).isoformat(),
                "reset_at": period_end(at).isoformat(),
            },
            "api_calls": {
                "used": rollup.api_calls,
                "limit": plan.quota_api_calls,
                "remaining": max(0, plan.quota_api_calls - rollup.api_calls),
            },
            "tokens": {
                # The quota counts every category; the breakdown keeps them
                # separate because that is what pricing needs (rule T5).
                "used": rollup.total_tokens,
                "limit": plan.quota_tokens,
                "remaining": max(0, plan.quota_tokens - rollup.total_tokens),
                "breakdown": {
                    "input_tokens": rollup.input_tokens,
                    "cached_input_tokens": rollup.cached_input_tokens,
                    "output_tokens": rollup.output_tokens,
                    "reasoning_tokens": rollup.reasoning_tokens,
                },
            },
            "cost": money_fields(rollup.cost_micro_cents),
            "event_count": rollup.event_count,
        }
