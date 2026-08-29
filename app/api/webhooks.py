"""Stripe webhook receiver.

Three non-negotiables, in order:

1. **Raw body.** Signature verification is over the exact bytes Stripe sent. This
   route never declares a Pydantic body model, so FastAPI does not parse or
   re-serialise the payload; we read ``await request.body()`` ourselves (rule W1).
2. **Verify before anything else.** A forged or stale signature returns 400 and
   writes nothing at all (rules W2, W3).
3. **Deduplicate on ``event.id``.** A replayed event is processed once (rule W4).

There is no API-key auth on this route by design: the Stripe signature *is* the
authentication.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, Request

from app.api.deps import SessionDep
from app.api.errors import error_response
from app.core.config import get_settings
from app.services.stripe_sync import StripeSyncService

log = logging.getLogger(__name__)

router = APIRouter(tags=["stripe"])


@router.post("/webhooks/stripe", summary="Signature-verified subscription sync")
async def stripe_webhook(
    request: Request,
    session: SessionDep,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
):
    settings = get_settings()

    if not settings.stripe_webhooks_configured:
        return error_response(
            503,
            "stripe_not_configured",
            "STRIPE_WEBHOOK_SECRET is not set. See .env.example.",
        )

    # 1. RAW bytes. Any re-encoding here breaks verification.
    payload = await request.body()

    if not stripe_signature:
        return error_response(
            400, "missing_signature", "Stripe-Signature header is required."
        )

    # 2. Verify. construct_event checks the v1 HMAC-SHA256 over "{t}.{payload}"
    #    and enforces the timestamp tolerance, which is why a captured-and-
    #    replayed request cannot be used indefinitely.
    import stripe

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=stripe_signature,
            secret=settings.stripe_webhook_secret,
            tolerance=settings.stripe_webhook_tolerance_seconds,
        )
    except ValueError:
        log.warning("webhook rejected: malformed payload")
        return error_response(400, "invalid_payload", "Malformed webhook payload.")
    except stripe.SignatureVerificationError:
        # Deliberately vague to the caller, explicit in our logs. Nothing has
        # been written at this point, and nothing will be.
        log.warning("webhook rejected: signature verification failed")
        return error_response(
            400, "invalid_signature", "Webhook signature verification failed."
        )

    # 3. Dedup + handle. Stripe wants a fast 2xx; the heavy rollup work is the
    #    background job's problem, not this request's (rule W6).
    # Verification passed, so the raw bytes are trustworthy: parse them into a
    # plain dict rather than depending on the SDK's object-to-dict helper, whose
    # name has changed across stripe-python versions.
    import json

    event_dict = event if isinstance(event, dict) else json.loads(payload)
    return StripeSyncService(session).handle_event(event_dict)
