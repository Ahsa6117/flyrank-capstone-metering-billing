"""SQLAlchemy ORM models -- the schema.

Money columns are BigInteger only. There is no Float or Numeric column anywhere
in this schema, by design (docs/REFERENCES.md M1).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.periods import now


class Base(DeclarativeBase):
    pass


class Plan(Base):
    __tablename__ = "plans"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    quota_api_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    quota_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)


class Tenant(Base):
    """One customer organisation. Every other row belongs to exactly one tenant."""

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    #: SHA-256 of the API key. The plaintext key is never stored (S6).
    api_key_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    plan_code: Mapped[str] = mapped_column(
        ForeignKey("plans.code"), nullable=False, default="free"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now
    )

    plan: Mapped[Plan] = relationship(lazy="joined")
    subscription: Mapped["Subscription | None"] = relationship(
        back_populates="tenant", uselist=False, lazy="joined"
    )


class Subscription(Base):
    """Mirror of Stripe's subscription state. Never the source of truth."""

    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, unique=True, index=True
    )
    plan_code: Mapped[str] = mapped_column(ForeignKey("plans.code"), nullable=False)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(128))
    stripe_subscription_id: Mapped[str | None] = mapped_column(
        String(128), unique=True, index=True
    )
    #: Mirrors Stripe's status verbatim: active, trialing, past_due, unpaid,
    #: canceled, incomplete, incomplete_expired.
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now, onupdate=now
    )

    tenant: Mapped[Tenant] = relationship(back_populates="subscription")


class UsageEvent(Base):
    """One recorded row of billable activity.

    The four token counters are stored separately and are never collapsed into a
    single column, because pricing needs them apart (rule T1).
    """

    __tablename__ = "usage_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)

    api_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    #: Integer micro-cents. Never a float (M1).
    cost_micro_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now
    )

    __table_args__ = (
        # Every rollup is "this tenant, this month".
        Index("ix_usage_events_tenant_occurred", "tenant_id", "occurred_at"),
    )


class IdempotencyKey(Base):
    """The no-double-count guarantee.

    The UNIQUE constraint on (tenant_id, idempotency_key) -- not application
    logic -- is what makes exactly-once metering true under concurrency. A
    check-then-insert has a race window between the two statements; a unique
    index has none (rule I5).

    Stores the status code and body of the first attempt so a replay returns a
    byte-identical response, including a replayed failure (rule I1).
    """

    __tablename__ = "idempotency_keys"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    #: SHA-256 of the canonical request body, so the same key with a different
    #: payload is a 409 rather than a silently wrong replay (rule I2).
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[str] = mapped_column(Text, nullable=False)
    usage_event_id: Mapped[str | None] = mapped_column(ForeignKey("usage_events.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_idempotency_tenant_key"
        ),
    )


class ProcessedWebhookEvent(Base):
    """Webhook replay protection, keyed on Stripe's ``event.id``.

    Stripe explicitly warns not to use ``created`` to decide whether an event was
    already processed, because delivery is at-least-once and unordered. Track the
    event ID instead (rule W4).
    """

    __tablename__ = "processed_webhook_events"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now
    )


class UsageAlert(Base):
    """Emitted by the background job at 80% / 100% of a quota.

    Unique per (tenant, usage type, threshold, period) so a job re-run cannot
    duplicate an alert -- the same idempotency discipline as metering.
    """

    __tablename__ = "usage_alerts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    usage_type: Mapped[str] = mapped_column(String(32), nullable=False)
    threshold_percent: Mapped[int] = mapped_column(Integer, nullable=False)
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "usage_type",
            "threshold_percent",
            "period_start",
            name="uq_usage_alert_once",
        ),
    )


class JobRun(Base):
    """Audit trail for the background job, including its failure alert (S3)."""

    __tablename__ = "job_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # success|failed
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


__all__ = [
    "Base",
    "Plan",
    "Tenant",
    "Subscription",
    "UsageEvent",
    "IdempotencyKey",
    "ProcessedWebhookEvent",
    "UsageAlert",
    "JobRun",
]
