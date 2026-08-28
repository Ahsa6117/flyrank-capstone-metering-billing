"""The single place domain errors become HTTP status codes.

Keeping this mapping in one file is what lets the service layer stay free of HTTP
concepts (requirement S1). It is also where the 402-vs-429 convention documented
in README.md is actually enforced.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.errors import (
    IdempotencyConflict,
    QuotaExceeded,
    SubscriptionNotActive,
    TenantNotFound,
)

log = logging.getLogger(__name__)


def error_response(
    status_code: int, code: str, message: str, /, **extra
) -> JSONResponse:
    payload = {"error": {"code": code, "message": message, **extra}}
    return JSONResponse(status_code=status_code, content=payload)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(QuotaExceeded)
    async def _quota(_: Request, exc: QuotaExceeded) -> JSONResponse:
        """429: the plan is healthy, this month's allowance is spent.

        Retry-After is the seconds until the quota window rolls over -- the
        moment a retry can actually succeed, rather than an arbitrary backoff
        (rule Q2).
        """
        response = error_response(
            429,
            "quota_exceeded",
            str(exc),
            usage_type=exc.usage_type,
            plan=exc.plan_code,
            used=exc.used,
            limit=exc.limit,
            requested=exc.requested,
            reset_at=exc.reset_at.isoformat(),
            upgrade_url="/v1/billing/checkout",
        )
        response.headers["Retry-After"] = str(exc.retry_after_seconds)
        return response

    @app.exception_handler(SubscriptionNotActive)
    async def _subscription(_: Request, exc: SubscriptionNotActive) -> JSONResponse:
        """402: the subscription itself is not in good standing.

        402 is a reserved, non-standard code with no standard convention, so ours
        is written down in README.md and DESIGN.md -- that is the honest way to
        use it (rule P1).
        """
        return error_response(
            402,
            "payment_required",
            f"Your subscription is {exc.status}. Update payment or resubscribe "
            f"before making billable requests.",
            subscription_status=exc.status,
            upgrade_url="/v1/billing/checkout",
        )

    @app.exception_handler(IdempotencyConflict)
    async def _conflict(_: Request, exc: IdempotencyConflict) -> JSONResponse:
        return error_response(
            409,
            "idempotency_key_reuse",
            str(exc),
            hint="Generate a new Idempotency-Key for a different request body.",
        )

    @app.exception_handler(TenantNotFound)
    async def _tenant(_: Request, exc: TenantNotFound) -> JSONResponse:
        return error_response(401, "unauthorized", "Unknown or missing API key.")

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        """422, never a 500 -- requirement S2."""
        return error_response(
            422,
            "validation_error",
            "Request body failed validation.",
            details=[
                {"field": ".".join(str(p) for p in e["loc"]), "problem": e["msg"]}
                for e in exc.errors()
            ],
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        """Last resort. Logs through the redacting filter, leaks nothing."""
        log.exception("unhandled error: %s", type(exc).__name__)
        return error_response(
            500, "internal_error", "An unexpected error occurred."
        )
