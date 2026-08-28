"""Stripe Checkout session creation (test mode only)."""

from __future__ import annotations

import logging

from fastapi import APIRouter

from app.api.deps import SessionDep, TenantDep
from app.api.errors import error_response
from app.api.schemas import CheckoutRequest
from app.core.config import get_settings
from app.repositories import TenantRepository

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/billing", tags=["billing"])


@router.post("/checkout", summary="Create a Stripe test-mode Checkout Session")
def create_checkout_session(
    payload: CheckoutRequest, tenant: TenantDep, session: SessionDep
):
    """Start an upgrade to Pro.

    This endpoint does NOT change the tenant's plan. It only creates a session.
    The plan flips when -- and only when -- a signature-verified
    ``checkout.session.completed` webhook arrives. Trusting the redirect back
    from Checkout would let anyone upgrade themselves by visiting a URL.
    """
    settings = get_settings()
    if not settings.stripe_secret_key or not settings.stripe_price_id_pro:
        return error_response(
            503,
            "stripe_not_configured",
            "STRIPE_SECRET_KEY and STRIPE_PRICE_ID_PRO must be set. "
            "See .env.example and README 'Stripe setup'.",
        )

    import stripe

    stripe.api_key = settings.stripe_secret_key

    existing = TenantRepository(session).get_subscription(tenant.id)
    customer_id = existing.stripe_customer_id if existing else None

    try:
        checkout = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": settings.stripe_price_id_pro, "quantity": 1}],
            success_url=settings.stripe_success_url,
            cancel_url=settings.stripe_cancel_url,
            # How the webhook will identify the tenant later. Set by us,
            # server-side -- never taken from client input.
            client_reference_id=tenant.id,
            metadata={"tenant_id": tenant.id, "plan_code": payload.plan_code},
            subscription_data={
                "metadata": {"tenant_id": tenant.id, "plan_code": payload.plan_code}
            },
            **({"customer": customer_id} if customer_id else {}),
        )
    except stripe.StripeError as exc:
        # exc.user_message never contains our key; the redacting log filter is
        # the backstop if a future Stripe error ever echoes one.
        log.warning("stripe checkout creation failed: %s", type(exc).__name__)
        return error_response(
            502, "stripe_error", "Could not create a Checkout session."
        )

    return {
        "checkout_session_id": checkout.id,
        "checkout_url": checkout.url,
        "tenant_id": tenant.id,
        "plan_code": payload.plan_code,
        "note": (
            "Test mode. Pay with card 4242 4242 4242 4242, any future expiry, "
            "any CVC. The plan changes only when the signed webhook arrives."
        ),
    }
