from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.periods import period_end, period_start
from app.models import UsageEvent


@dataclass(frozen=True, slots=True)
class UsageRollup:
    """Aggregate of many usage events into one summary for a billing period."""

    api_calls: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cost_micro_cents: int
    event_count: int

    @property
    def total_tokens(self) -> int:
        """All four categories summed -- for QUOTA counting only, never pricing."""
        return (
            self.input_tokens
            + self.cached_input_tokens
            + self.output_tokens
            + self.reasoning_tokens
        )


class UsageRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, event: UsageEvent) -> UsageEvent:
        self.session.add(event)
        # Flush, do not commit: the caller owns the transaction so that the usage
        # event and its idempotency record land together or not at all.
        self.session.flush()
        return event

    def new_id(self) -> str:
        return f"ue_{uuid.uuid4().hex}"

    def rollup(self, tenant_id: str, at: datetime | None = None) -> UsageRollup:
        """Month-to-date totals for ONE tenant. tenant_id is not optional."""
        start, end = period_start(at), period_end(at)

        row = self.session.execute(
            select(
                func.coalesce(func.sum(UsageEvent.api_calls), 0),
                func.coalesce(func.sum(UsageEvent.input_tokens), 0),
                func.coalesce(func.sum(UsageEvent.cached_input_tokens), 0),
                func.coalesce(func.sum(UsageEvent.output_tokens), 0),
                func.coalesce(func.sum(UsageEvent.reasoning_tokens), 0),
                func.coalesce(func.sum(UsageEvent.cost_micro_cents), 0),
                func.count(UsageEvent.id),
            ).where(
                UsageEvent.tenant_id == tenant_id,
                UsageEvent.occurred_at >= start,
                UsageEvent.occurred_at < end,
            )
        ).one()

        return UsageRollup(
            api_calls=int(row[0]),
            input_tokens=int(row[1]),
            cached_input_tokens=int(row[2]),
            output_tokens=int(row[3]),
            reasoning_tokens=int(row[4]),
            cost_micro_cents=int(row[5]),
            event_count=int(row[6]),
        )

    def count_events(self, tenant_id: str) -> int:
        """All-time event count for a tenant -- used by tests and evidence probes."""
        return int(
            self.session.scalar(
                select(func.count(UsageEvent.id)).where(
                    UsageEvent.tenant_id == tenant_id
                )
            )
            or 0
        )
