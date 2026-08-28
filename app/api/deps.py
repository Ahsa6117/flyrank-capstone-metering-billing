"""Shared HTTP dependencies: tenant auth and the Idempotency-Key header."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.core.errors import TenantNotFound
from app.db import get_session
from app.models import Tenant
from app.repositories import TenantRepository

SessionDep = Annotated[Session, Depends(get_session)]

#: Stripe caps idempotency keys at 255 characters; so do we (rule I3).
MAX_IDEMPOTENCY_KEY_LENGTH = 255


def get_current_tenant(
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Tenant:
    """Resolve the tenant from ``Authorization: Bearer <api key>``.

    Only the SHA-256 of the key is stored, so authentication is a hash lookup and
    the plaintext key never appears in the database or the logs (S6).
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise TenantNotFound("Missing bearer token")

    api_key = authorization.split(" ", 1)[1].strip()
    tenant = TenantRepository(session).get_by_api_key(api_key)
    if tenant is None:
        raise TenantNotFound("Unknown API key")
    return tenant


TenantDep = Annotated[Tenant, Depends(get_current_tenant)]


def get_idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    """Require a client-generated idempotency key on every billable request.

    Stricter than Stripe, which treats the header as optional: here every call to
    this route costs money, so an un-keyed request has no safe retry story. We
    reject it rather than meter something that cannot be deduplicated.
    """
    if not idempotency_key or not idempotency_key.strip():
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "idempotency_key_required",
                    "message": (
                        "The Idempotency-Key header is required on billable "
                        "requests. Send a client-generated UUIDv4."
                    ),
                }
            },
        )

    key = idempotency_key.strip()
    if len(key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "idempotency_key_too_long",
                    "message": (
                        f"Idempotency-Key must be at most "
                        f"{MAX_IDEMPOTENCY_KEY_LENGTH} characters."
                    ),
                }
            },
        )
    return key


IdempotencyKeyDep = Annotated[str, Depends(get_idempotency_key)]
