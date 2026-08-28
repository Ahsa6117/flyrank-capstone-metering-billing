from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import IdempotencyKey


def fingerprint(payload: Any) -> str:
    """SHA-256 over a canonical JSON rendering of the request body.

    ``sort_keys`` and a fixed separator make the fingerprint stable regardless of
    key order or whitespace, so a semantically identical retry matches while a
    genuinely different payload does not (rule I2).
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class IdempotencyRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, tenant_id: str, key: str) -> IdempotencyKey | None:
        return self.session.scalar(
            select(IdempotencyKey).where(
                IdempotencyKey.tenant_id == tenant_id,
                IdempotencyKey.idempotency_key == key,
            )
        )

    def add(
        self,
        *,
        tenant_id: str,
        key: str,
        request_fingerprint: str,
        status_code: int,
        response_body: str,
        usage_event_id: str | None,
    ) -> IdempotencyKey:
        """Insert the record whose UNIQUE index enforces exactly-once metering.

        Flushes rather than commits so the caller can keep this in the same
        transaction as the usage event. A concurrent twin hits the unique
        constraint here and raises IntegrityError -- that is the intended,
        load-bearing behaviour, not an error to be avoided (rule I5).
        """
        record = IdempotencyKey(
            id=f"idem_{uuid.uuid4().hex}",
            tenant_id=tenant_id,
            idempotency_key=key,
            request_fingerprint=request_fingerprint,
            status_code=status_code,
            response_body=response_body,
            usage_event_id=usage_event_id,
        )
        self.session.add(record)
        self.session.flush()
        return record
