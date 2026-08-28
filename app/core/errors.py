"""Domain errors.

These carry no HTTP status codes on purpose. The service layer raises them; a
single mapper in ``app/api/errors.py`` decides that QuotaExceeded means 429 and
SubscriptionNotActive means 402. That is what keeps the layers separate (S1).
"""

from __future__ import annotations

from datetime import datetime


class BillingError(Exception):
    """Base class for every domain error in this service."""


class TenantNotFound(BillingError):
    pass


class SubscriptionNotActive(BillingError):
    """The plan itself is not in good standing -- money must change. Maps to 402.

    Checked BEFORE quota, because telling an unpaid customer "you are out of
    quota" would be a lie (rule P3).
    """

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"Subscription is not active (status: {status})")


class QuotaExceeded(BillingError):
    """Plan is healthy, the monthly allowance is spent. Maps to 429."""

    def __init__(
        self,
        *,
        usage_type: str,
        used: int,
        limit: int,
        requested: int,
        reset_at: datetime,
        retry_after_seconds: int,
        plan_code: str,
    ) -> None:
        self.usage_type = usage_type
        self.used = used
        self.limit = limit
        self.requested = requested
        self.reset_at = reset_at
        self.retry_after_seconds = retry_after_seconds
        self.plan_code = plan_code
        super().__init__(
            f"{usage_type} quota exceeded: {used} used + {requested} requested "
            f"exceeds the {plan_code} plan limit of {limit}"
        )


class IdempotencyConflict(BillingError):
    """Same idempotency key, different request body. Maps to 409.

    Stripe's idempotency layer "compares incoming parameters to those of the
    original request and errors if they're not the same" (rule I2). Silently
    returning the old response for a different payload would be worse than an
    error: the caller would believe work was done that never was.
    """

    def __init__(self, idempotency_key: str) -> None:
        self.idempotency_key = idempotency_key
        super().__init__(
            f"Idempotency key {idempotency_key!r} was already used with a "
            f"different request body"
        )
