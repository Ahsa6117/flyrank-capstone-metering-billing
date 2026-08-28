from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import ProcessedWebhookEvent


class WebhookRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def already_processed(self, event_id: str) -> bool:
        return self.session.get(ProcessedWebhookEvent, event_id) is not None

    def mark_processed(self, event_id: str, event_type: str) -> ProcessedWebhookEvent:
        """Record the event id so a redelivery is ignored.

        Keyed on ``event.id``, never on ``created`` and never on payload equality:
        Stripe delivers at-least-once and out of order, and distinct events can
        share a timestamp (rule W4).
        """
        record = ProcessedWebhookEvent(event_id=event_id, event_type=event_type)
        self.session.add(record)
        self.session.flush()
        return record
