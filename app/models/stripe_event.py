"""StripeEvent — every webhook event we've seen, for deduplication.

Stripe retries webhook deliveries on non-2xx responses, and for at-least-once
semantics we need to dedupe. We insert a row per event id; a unique index
makes replays fail fast and cheaply.

We store the event type and a small amount of status info for audit. We do
not store the full payload — that's available from Stripe for 30 days and
saves us worrying about PII retention.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models._base import IdMixin, utcnow


class StripeEvent(db.Model, IdMixin):
    __tablename__ = "stripe_events"

    # Stripe's event id, e.g. "evt_1NtMabc..."
    event_id: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)

    # "received" when we first see it, "handled"/"skipped"/"errored" after
    processing_result: Mapped[str] = mapped_column(String(20), default="received")
    note: Mapped[str | None] = mapped_column(String(500))

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )

    def __repr__(self) -> str:
        return f"<StripeEvent {self.event_id} {self.event_type}>"
