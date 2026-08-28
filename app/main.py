"""Application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from app.api.errors import register_error_handlers
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db import run_migrations

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)

    applied = run_migrations()
    if applied:
        log.info("applied migrations: %s", ", ".join(applied))

    if not settings.stripe_configured:
        # Not fatal: metering, quotas and cost all work without Stripe. Only the
        # checkout and webhook routes need keys.
        log.warning(
            "Stripe is not configured (STRIPE_SECRET_KEY / STRIPE_WEBHOOK_SECRET "
            "missing). Metering, quota and cost endpoints work; billing endpoints "
            "will return 503."
        )

    from app.jobs.scheduler import start_scheduler, stop_scheduler

    scheduler = start_scheduler()
    try:
        yield
    finally:
        stop_scheduler(scheduler)


app = FastAPI(
    title="Usage Metering & Billing Engine",
    version="1.0.0",
    description=(
        "Metering with exactly-once guarantees, quota enforcement, integer money "
        "math, and signature-verified Stripe test-mode webhooks."
    ),
    lifespan=lifespan,
)

register_error_handlers(app)


@app.exception_handler(HTTPException)
async def _http_exception(_, exc: HTTPException) -> JSONResponse:
    """Keep hand-raised HTTPExceptions in the same {"error": {...}} shape."""
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        return JSONResponse(status_code=exc.status_code, content=detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": "http_error", "message": str(detail)}},
    )


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}


from app.api import billing, internal, routes, webhooks  # noqa: E402

app.include_router(routes.router)
app.include_router(billing.router)
app.include_router(webhooks.router)
app.include_router(internal.router)
