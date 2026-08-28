"""Seed demo data: run migrations, create three tenants, print their API keys.

Keys are deterministic so the README, EVIDENCE.md and the acceptance probes can
all refer to the same values. These are **demo keys for a local SQLite database**,
not secrets -- no Stripe key, password or production credential is ever printed
or stored by this script.

    python seed.py
"""

from __future__ import annotations

import uuid

from app.db import SessionLocal, run_migrations
from app.models import Subscription, Tenant
from app.repositories.tenants import hash_api_key

#: (tenant id, display name, plan, demo API key, subscription status or None)
DEMO_TENANTS = [
    ("tnt_acme", "Acme Corp", "free", "demo_key_acme_free", None),
    ("tnt_globex", "Globex Inc", "pro", "demo_key_globex_pro", "active"),
    # Deliberately past_due: this is the tenant that demonstrates 402, and proves
    # 402 wins over 429 even when quota remains.
    ("tnt_initech", "Initech LLC", "pro", "demo_key_initech_pastdue", "past_due"),
]


def seed() -> None:
    applied = run_migrations()
    print(f"migrations applied: {applied or 'none pending'}")

    session = SessionLocal()
    try:
        for tenant_id, name, plan_code, api_key, sub_status in DEMO_TENANTS:
            tenant = session.get(Tenant, tenant_id)
            if tenant is None:
                tenant = Tenant(
                    id=tenant_id,
                    name=name,
                    api_key_hash=hash_api_key(api_key),
                    plan_code=plan_code,
                )
                session.add(tenant)
            else:
                tenant.name = name
                tenant.plan_code = plan_code
                tenant.api_key_hash = hash_api_key(api_key)

            if sub_status is not None:
                existing = (
                    session.query(Subscription).filter_by(tenant_id=tenant_id).first()
                )
                if existing is None:
                    session.add(
                        Subscription(
                            id=f"sub_{uuid.uuid4().hex}",
                            tenant_id=tenant_id,
                            plan_code=plan_code,
                            status=sub_status,
                        )
                    )
                else:
                    existing.status = sub_status
                    existing.plan_code = plan_code

        session.commit()
    finally:
        session.close()

    print("\nDemo tenants (local database only -- not secrets):\n")
    print(f"  {'API key':<28} {'tenant':<14} {'plan':<6} subscription")
    print(f"  {'-' * 28} {'-' * 14} {'-' * 6} {'-' * 12}")
    for tenant_id, _name, plan_code, api_key, sub_status in DEMO_TENANTS:
        print(f"  {api_key:<28} {tenant_id:<14} {plan_code:<6} {sub_status or 'none'}")

    print("\nTry it:")
    print('  curl -s http://localhost:8000/v1/usage \\')
    print('    -H "Authorization: Bearer demo_key_acme_free"')


if __name__ == "__main__":
    seed()
