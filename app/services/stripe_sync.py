"""Subscription sync from verified Stripe events.

Payment truth lives at Stripe. This service is the ONLY thing allowed to change a
tenant's plan, and it only ever acts on an event whose signature has already been
verified by the HTTP layer.

Two properties matter more than the handlers themselves:

* **Deduplication by ``event.id``.** Delivery is at-least-once; a replayed event
  must be processed once (rule W4).
* **Order independence.** Stripe does not guarantee ordering, so no handler may
  assume a predecessor arrived. We write the status the event carries and derive
  the plan from it, rather than stepping a state machine (rule W5).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Subscription
from app.repositories import TenantRepository, WebhookRepository

log = logging.getLogger(__name__)

#: Events this integration acts on. Anything else is a deliberate 200 no-op --
#: returning an error would make Stripe retry it for three days (rule W7).
HANDLED_EVENTS = frozenset(
    {
        "checkout.session.completed",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    }
)

#: Statuses under which the tenant keeps their paid plan.
PAID_STATUSES = frozenset({"active", "trialing"})


class StripeSyncService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.tenants = TenantRepository(session)
        self.webhooks = WebhookRepository(session)

    def handle_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Process one verified event, exactly once."""
        event_id = event["id"]
        event_type = event["type"]

        if self.webhooks.already_processed(event_id):
            log.info("duplicate webhook ignored: %s (%s)", event_id, event_type)
            return {"status": "duplicate_ignored", "event_id": event_id}

        if event_type not in HANDLED_EVENTS:
            # Record it anyway so a redelivery is also a fast no-op.
            self._mark_and_commit(event_id, event_type)
            return {"status": "ignored_unhandled_type", "event_type": event_type}

        obj = event["data"]["object"]
        try:
            if event_type == "checkout.session.completed":
                outcome = self._on_checkout_completed(obj)
            elif event_type == "customer.subscription.updated":
                outcome = self._on_subscription_changed(obj)
            else:  # customer.subscription.deleted
                outcome = self._on_subscription_deleted(obj)

            self.webhooks.mark_processed(event_id, event_type)
            self.session.commit()
        except IntegrityError:
            # Two deliveries of the same event raced. The unique primary key on
            # processed_webhook_events rejected the second, so it changed nothing.
            self.session.rollback()
            log.info("webhook race lost, already processed: %s", event_id)
            return {"status": "duplicate_ignored", "event_id": event_id}

        return {"status": "processed", "event_id": event_id, **outcome}

    # --- handlers -----------------------------------------------------------

    def _on_checkout_completed(self, session_obj: dict[str, Any]) -> dict[str, Any]:
        """A test Checkout finished: flip the tenant to Pro.

        The tenant is identified from ``client_reference_id`` / metadata that we
        set when creating the session -- never from anything the browser supplies.
        """
        tenant_id = session_obj.get("client_reference_id") or (
            session_obj.get("metadata") or {}
        ).get("tenant_id")
        if not tenant_id:
            log.warning("checkout.session.completed without a tenant reference")
            return {"action": "skipped_no_tenant_reference"}

        plan_code = (session_obj.get("metadata") or {}).get("plan_code", "pro")

        self._upsert_subscription(
            tenant_id=tenant_id,
            plan_code=plan_code,
            stripe_customer_id=session_obj.get("customer"),
            stripe_subscription_id=session_obj.get("subscription"),
            status="active",
            current_period_end=None,
        )
        self.tenants.set_plan(tenant_id, plan_code)
        log.info("tenant %s upgraded to %s via checkout", tenant_id, plan_code)
        return {"action": "plan_upgraded", "tenant_id": tenant_id, "plan": plan_code}

    def _on_subscription_changed(self, sub_obj: dict[str, Any]) -> dict[str, Any]:
        """Mirror a status change: active, past_due, unpaid, canceled...

        A tenant whose subscription lapses drops back to Free limits, and their
        billable calls start returning 402 until payment is fixed.
        """
        subscription = self._locate(sub_obj)
        if subscription is None:
            return {"action": "skipped_unknown_subscription"}

        status = sub_obj.get("status", "active")
        subscription.status = status
        subscription.current_period_end = _epoch_to_dt(
            sub_obj.get("current_period_end")
        )

        plan_code = subscription.plan_code if status in PAID_STATUSES else "free"
        self.tenants.set_plan(subscription.tenant_id, plan_code)

        log.info(
            "subscription %s now %s; tenant %s on plan %s",
            subscription.id,
            status,
            subscription.tenant_id,
            plan_code,
        )
        return {
            "action": "subscription_synced",
            "tenant_id": subscription.tenant_id,
            "status": status,
            "plan": plan_code,
        }

    def _on_subscription_deleted(self, sub_obj: dict[str, Any]) -> dict[str, Any]:
        subscription = self._locate(sub_obj)
        if subscription is None:
            return {"action": "skipped_unknown_subscription"}

        subscription.status = "canceled"
        self.tenants.set_plan(subscription.tenant_id, "free")
        log.info("subscription %s canceled; tenant downgraded", subscription.id)
        return {
            "action": "subscription_canceled",
            "tenant_id": subscription.tenant_id,
            "plan": "free",
        }

    # --- helpers ------------------------------------------------------------

    def _locate(self, sub_obj: dict[str, Any]) -> Subscription | None:
        """Find our mirror row by Stripe subscription id, then by customer id.

        The customer-id fallback is what makes the handlers order-independent: a
        subscription.updated that arrives before we ever stored a subscription id
        can still be matched (rule W5).
        """
        found = self.tenants.get_subscription_by_stripe_id(sub_obj["id"])
        if found is not None:
            return found

        customer_id = sub_obj.get("customer")
        if customer_id:
            found = self.tenants.get_by_stripe_customer_id(customer_id)
            if found is not None:
                found.stripe_subscription_id = sub_obj["id"]
                return found

        tenant_id = (sub_obj.get("metadata") or {}).get("tenant_id")
        if tenant_id:
            return self.tenants.get_subscription(tenant_id)

        log.warning("could not match Stripe subscription %s to a tenant", sub_obj["id"])
        return None

    def _upsert_subscription(
        self,
        *,
        tenant_id: str,
        plan_code: str,
        stripe_customer_id: str | None,
        stripe_subscription_id: str | None,
        status: str,
        current_period_end: datetime | None,
    ) -> Subscription:
        existing = self.tenants.get_subscription(tenant_id)
        if existing is None:
            existing = Subscription(
                id=f"sub_{uuid.uuid4().hex}", tenant_id=tenant_id, plan_code=plan_code
            )
            self.session.add(existing)

        existing.plan_code = plan_code
        existing.status = status
        if stripe_customer_id:
            existing.stripe_customer_id = stripe_customer_id
        if stripe_subscription_id:
            existing.stripe_subscription_id = stripe_subscription_id
        if current_period_end:
            existing.current_period_end = current_period_end

        self.session.flush()
        return existing

    def _mark_and_commit(self, event_id: str, event_type: str) -> None:
        try:
            self.webhooks.mark_processed(event_id, event_type)
            self.session.commit()
        except IntegrityError:  # pragma: no cover - concurrent duplicate
            self.session.rollback()


def _epoch_to_dt(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value, tz=timezone.utc) if value else None
