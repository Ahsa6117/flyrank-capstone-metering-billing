"""Data access. The only place SQLAlchemy queries are written.

Tenant isolation (requirement R10 / S4) is enforced structurally: there is no
method here that reads or writes usage without a ``tenant_id`` argument. A
caller cannot accidentally query across tenants because the API does not exist.
"""

from app.repositories.idempotency import IdempotencyRepository
from app.repositories.tenants import TenantRepository
from app.repositories.usage import UsageRepository
from app.repositories.webhooks import WebhookRepository

__all__ = [
    "TenantRepository",
    "UsageRepository",
    "IdempotencyRepository",
    "WebhookRepository",
]
