"""MeterService -- the heart of the capstone.

    Guarantee: the same (tenant_id, idempotency_key) produces exactly one
    usage_event, ever.

Why this is correct rather than merely careful:

* The **unique index** on (tenant_id, idempotency_key) is the arbiter, not a
  SELECT before an INSERT. A check-then-insert has a race window between the two
  statements; a unique constraint has none. Under concurrent retries one
  transaction commits and the other gets IntegrityError -- and the loser returns
  the winner's stored response instead of creating a second event.
* The usage event and its idempotency record are inserted in the **same
  transaction**, so there is no state in which a request was billed but not
  recorded as billed.
* We store the **status code and body** of the first attempt, so a replay is
  byte-identical (rule I1).
* The request **fingerprint** means a client cannot reuse a key with a different
  payload and silently receive the old answer (rule I2).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import IdempotencyConflict
from app.core.money import money_fields
from app.core.periods import now
from app.core.pricing import TokenUsage, price_event
from app.models import Tenant, UsageEvent
from app.repositories import IdempotencyRepository, UsageRepository
from app.repositories.idempotency import fingerprint
from app.services.quota import QuotaService

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MeterResult:
    """What the HTTP layer needs. ``replayed`` drives ``idempotent_replay``."""

    status_code: int
    body: dict[str, Any]
    replayed: bool


class MeterService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.usage = UsageRepository(session)
        self.idempotency = IdempotencyRepository(session)
        self.quota = QuotaService(session)

    def record(
        self,
        tenant: Tenant,
        *,
        event_type: str,
        api_calls: int,
        tokens: TokenUsage,
        idempotency_key: str,
        request_payload: Any,
        at: datetime | None = None,
    ) -> MeterResult:
        """Meter one billable action, exactly once.

        Order of operations (see docs/DESIGN.md §7):
          1. replay check      -> stored response, or 409 on a different body
          2. subscription gate -> 402
          3. quota gate        -> 429
          4. price             -> integer micro-cents
          5. one transaction   -> usage_event + idempotency_key, or neither
        """
        request_fp = fingerprint(request_payload)

        # --- 1. replay check -------------------------------------------------
        existing = self.idempotency.get(tenant.id, idempotency_key)
        if existing is not None:
            return self._replay(existing, request_fp, idempotency_key)

        # --- 2 & 3. gates. Both raise before anything is written. ------------
        # 402 is checked first, deliberately: an unpaid tenant must not be told
        # they are out of quota (rule P3).
        self.quota.assert_subscription_active(tenant)
        self.quota.assert_within_quota(
            tenant,
            requested_api_calls=api_calls,
            requested_tokens=tokens,
            at=at,
        )

        # --- 4. price, in integer micro-cents --------------------------------
        cost = price_event(api_calls, tokens)

        # --- 5. one transaction ----------------------------------------------
        event = UsageEvent(
            id=self.usage.new_id(),
            tenant_id=tenant.id,
            event_type=event_type,
            api_calls=api_calls,
            input_tokens=tokens.input_tokens,
            cached_input_tokens=tokens.cached_input_tokens,
            output_tokens=tokens.output_tokens,
            reasoning_tokens=tokens.reasoning_tokens,
            cost_micro_cents=cost,
            idempotency_key=idempotency_key,
            occurred_at=at or now(),
        )

        body = {
            "usage_event_id": event.id,
            "tenant_id": tenant.id,
            "event_type": event_type,
            "billed": {
                "api_calls": api_calls,
                "input_tokens": tokens.input_tokens,
                "cached_input_tokens": tokens.cached_input_tokens,
                "output_tokens": tokens.output_tokens,
                "reasoning_tokens": tokens.reasoning_tokens,
            },
            "cost": money_fields(cost),
            "idempotent_replay": False,
        }

        try:
            self.usage.add(event)
            self.idempotency.add(
                tenant_id=tenant.id,
                key=idempotency_key,
                request_fingerprint=request_fp,
                status_code=200,
                response_body=json.dumps(body),
                usage_event_id=event.id,
            )
            self.session.commit()
        except IntegrityError:
            # A concurrent request with the same key won the race. The unique
            # index rejected our insert, so nothing of ours was written. Roll
            # back and return the winner's stored response: still exactly one
            # usage event for this key (rule I5).
            self.session.rollback()
            log.info(
                "idempotency race lost for tenant=%s key=%s; returning stored response",
                tenant.id,
                idempotency_key,
            )
            winner = self.idempotency.get(tenant.id, idempotency_key)
            if winner is None:  # pragma: no cover - would mean a different constraint
                raise
            return self._replay(winner, request_fp, idempotency_key)

        return MeterResult(status_code=200, body=body, replayed=False)

    @staticmethod
    def _replay(record, request_fp: str, idempotency_key: str) -> MeterResult:
        """Return the first attempt's stored response verbatim."""
        if record.request_fingerprint != request_fp:
            # Same key, different payload. Stripe's idempotency layer errors here
            # rather than returning a mismatched result, and so do we (rule I2).
            raise IdempotencyConflict(idempotency_key)

        body = json.loads(record.response_body)
        body["idempotent_replay"] = True
        return MeterResult(status_code=record.status_code, body=body, replayed=True)
