from __future__ import annotations

import hashlib

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Plan, Subscription, Tenant


def hash_api_key(api_key: str) -> str:
    """SHA-256 of an API key. Only the hash is ever stored or logged (S6)."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


class TenantRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_api_key(self, api_key: str) -> Tenant | None:
        """Authenticate by hash comparison -- the plaintext key is never stored."""
        return self.session.scalar(
            select(Tenant).where(Tenant.api_key_hash == hash_api_key(api_key))
        )

    def get(self, tenant_id: str) -> Tenant | None:
        return self.session.get(Tenant, tenant_id)

    def get_plan(self, plan_code: str) -> Plan | None:
        return self.session.get(Plan, plan_code)

    def list_all(self) -> list[Tenant]:
        """Used only by the background job, which legitimately spans tenants."""
        return list(self.session.scalars(select(Tenant)))

    def get_subscription(self, tenant_id: str) -> Subscription | None:
        return self.session.scalar(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        )

    def get_subscription_by_stripe_id(
        self, stripe_subscription_id: str
    ) -> Subscription | None:
        return self.session.scalar(
            select(Subscription).where(
                Subscription.stripe_subscription_id == stripe_subscription_id
            )
        )

    def get_by_stripe_customer_id(self, customer_id: str) -> Subscription | None:
        return self.session.scalar(
            select(Subscription).where(Subscription.stripe_customer_id == customer_id)
        )

    def lock_for_metering(self, tenant_id: str) -> None:
        """Serialise metering for one tenant until the transaction commits.

        The quota check is a read followed by a write, and without this the two
        can interleave: two callers both read "999 used, one left" and both
        insert. Measured 1006/1000 with 12 concurrent requests before this
        existed.

        Bumping a column is what takes the lock -- the value is never read. On
        Postgres this is a row lock, so only requests for the SAME tenant
        contend. On SQLite it is the database write lock, which is coarser but
        equally correct.

        This must be the FIRST write in the transaction. Locking after the quota
        read would be pointless: the stale read would already have happened.
        """
        self.session.execute(
            update(Tenant)
            .where(Tenant.id == tenant_id)
            .values(metering_lock=Tenant.metering_lock + 1)
        )

    def set_plan(self, tenant_id: str, plan_code: str) -> None:
        tenant = self.session.get(Tenant, tenant_id)
        if tenant is not None:
            tenant.plan_code = plan_code
