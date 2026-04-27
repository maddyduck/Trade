"""Booking — the core transactional entity.

Key design choices:
- No Customer table. Customer details are embedded here. If the same person
  books twice, they're two rows. Denormalise into a Customer table in v2
  if repeat-customer analytics become a feature.
- Status is an Enum; transitions are validated in the service layer, not here.
- Payment state is separate from booking state — a booking is 'booked' once
  Stripe confirms, so PaymentStatus.PAID is the gate that promotes PENDING_PAYMENT
  to BOOKED.
- Every status change writes a BookingEvent for audit.
- Partial unique index prevents double-booking the same (trade, start_at) slot
  — but only for active statuses. Cancelled bookings don't block the slot.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models._base import IdMixin, TimestampMixin, utcnow

if TYPE_CHECKING:
    from app.models.service import Service
    from app.models.trade import Trade


class BookingStatus(str, enum.Enum):
    """Lifecycle of a booking.

    PENDING_PAYMENT: customer has picked a slot but not yet paid the deposit.
        Held briefly (15 mins) while Stripe Checkout is open; auto-expired after.
    BOOKED:          deposit captured, slot locked.
    ON_THE_WAY:      trade has tapped "I'm on my way" in the dashboard.
    ARRIVED:         trade has arrived at the address.
    IN_PROGRESS:     work is underway.
    COMPLETE:        trade has marked the job finished.
    CANCELLED:       customer or trade cancelled. Refund handled separately.
    NO_SHOW:         customer didn't appear. Deposit retained per policy.
    """

    PENDING_PAYMENT = "pending_payment"
    BOOKED = "booked"
    ON_THE_WAY = "on_the_way"
    ARRIVED = "arrived"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


# Statuses that should reserve a slot (can't be double-booked).
ACTIVE_BOOKING_STATUSES = {
    BookingStatus.PENDING_PAYMENT,
    BookingStatus.BOOKED,
    BookingStatus.ON_THE_WAY,
    BookingStatus.ARRIVED,
    BookingStatus.IN_PROGRESS,
    BookingStatus.COMPLETE,  # complete still blocks the slot (it happened)
}

# Statuses that advance the job on the day.
IN_FLIGHT_STATUSES = {
    BookingStatus.ON_THE_WAY,
    BookingStatus.ARRIVED,
    BookingStatus.IN_PROGRESS,
}

TERMINAL_STATUSES = {
    BookingStatus.COMPLETE,
    BookingStatus.CANCELLED,
    BookingStatus.NO_SHOW,
}


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    REFUNDED = "refunded"
    PARTIAL_REFUND = "partial_refund"
    FAILED = "failed"


class Booking(db.Model, IdMixin, TimestampMixin):
    __tablename__ = "bookings"

    # Relations
    trade_id: Mapped[int] = mapped_column(
        ForeignKey("trades.id", ondelete="CASCADE"), nullable=False, index=True
    )
    service_id: Mapped[int] = mapped_column(
        ForeignKey("services.id"), nullable=False, index=True
    )

    # Public-facing reference, e.g. "MMK-4827-3F"
    reference: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)

    # Scheduling (stored UTC)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Customer details (embedded — no Customer table in V1)
    customer_name: Mapped[str] = mapped_column(String(120), nullable=False)
    customer_phone: Mapped[str] = mapped_column(String(40), nullable=False)
    customer_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    customer_postcode: Mapped[str] = mapped_column(String(16), nullable=False)
    customer_address: Mapped[str | None] = mapped_column(String(255))
    job_notes: Mapped[str | None] = mapped_column(Text)
    photo_urls: Mapped[str | None] = mapped_column(Text)  # newline-separated URLs

    # Money (pence, always)
    deposit_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="GBP", nullable=False)

    # Status.
    # values_callable=... is CRITICAL: without it SQLAlchemy stores the enum
    # name (uppercase, e.g. 'PENDING_PAYMENT') but our partial unique index
    # below checks for the lowercase value. Mismatch would silently disable
    # the double-booking guard.
    status: Mapped[BookingStatus] = mapped_column(
        db.Enum(
            BookingStatus,
            name="booking_status",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        default=BookingStatus.PENDING_PAYMENT,
        nullable=False,
        index=True,
    )
    payment_status: Mapped[PaymentStatus] = mapped_column(
        db.Enum(
            PaymentStatus,
            name="payment_status",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        default=PaymentStatus.PENDING,
        nullable=False,
    )

    # Stripe tracking
    stripe_checkout_session_id: Mapped[str | None] = mapped_column(String(200), index=True)
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String(200), index=True)
    stripe_charge_id: Mapped[str | None] = mapped_column(String(200))
    stripe_refund_id: Mapped[str | None] = mapped_column(String(200))

    # Trade's private notes, only visible in dashboard
    internal_notes: Mapped[str | None] = mapped_column(Text)

    # Relationships
    trade: Mapped[Trade] = relationship(back_populates="bookings")
    service: Mapped[Service] = relationship(back_populates="bookings")
    events: Mapped[list[BookingEvent]] = relationship(
        back_populates="booking",
        cascade="all, delete-orphan",
        order_by="BookingEvent.created_at",
    )
    invoices = relationship(
        "Invoice",
        back_populates="booking",
        cascade="all, delete-orphan",
        order_by="Invoice.created_at.desc()",
    )

    __table_args__ = (
        # Prevent double-booking: no two active bookings can share (trade_id, start_at).
        # Partial index — cancelled/no-show bookings don't count.
        Index(
            "ix_bookings_trade_start_active",
            "trade_id",
            "start_at",
            unique=True,
            postgresql_where=text(
                "status IN ('pending_payment','booked','on_the_way',"
                "'arrived','in_progress','complete')"
            ),
        ),
        Index("ix_bookings_trade_status_start", "trade_id", "status", "start_at"),
    )

    # --- Helpers ---
    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_BOOKING_STATUSES

    @property
    def is_paid(self) -> bool:
        return self.payment_status in (PaymentStatus.PAID, PaymentStatus.PARTIAL_REFUND)

    @property
    def deposit_display(self) -> str:
        return f"£{self.deposit_pence / 100:.0f}" if self.deposit_pence % 100 == 0 else f"£{self.deposit_pence / 100:.2f}"

    @property
    def photo_url_list(self) -> list[str]:
        if not self.photo_urls:
            return []
        return [u.strip() for u in self.photo_urls.split("\n") if u.strip()]

    @property
    def is_recently_booked(self) -> bool:
        """True if the booking was created in the last 18 hours.

        Used to show a 'new' marker on the trade's dashboard so they notice
        incoming work. 18 hours chosen so that a booking made overnight is
        still flagged when the trade opens the app in the morning.
        """
        from datetime import UTC, datetime, timedelta

        created = self.created_at
        if created.tzinfo is None:
            # Defensive: SQLite strips tz, treat as UTC.
            created = created.replace(tzinfo=UTC)
        return (datetime.now(UTC) - created) < timedelta(hours=18)

    def __repr__(self) -> str:
        return f"<Booking {self.reference} trade={self.trade_id} status={self.status.value}>"


class BookingEvent(db.Model, IdMixin):
    """Append-only audit log for a booking.

    Every state transition, every payment event, every significant action.
    created_at is set on insert; there's no updated_at because these rows
    are immutable by convention.
    """

    __tablename__ = "booking_events"

    booking_id: Mapped[int] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # e.g. "status_changed", "payment_captured", "note_added", "refund_issued"
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)

    # who did it — 'customer', 'trade', 'system', 'stripe'
    actor: Mapped[str] = mapped_column(String(20), nullable=False, default="system")

    # Free-text description for the audit log view
    description: Mapped[str | None] = mapped_column(Text)

    # Structured fields for status transitions
    from_status: Mapped[str | None] = mapped_column(String(40))
    to_status: Mapped[str | None] = mapped_column(String(40))

    # IP / user-agent when relevant (customer action)
    ip_address: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    booking: Mapped[Booking] = relationship(back_populates="events")

    def __repr__(self) -> str:
        return f"<BookingEvent {self.event_type} booking={self.booking_id}>"
