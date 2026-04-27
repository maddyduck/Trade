"""Invoice model — generated post-completion of a booking.

Lifecycle:
  DRAFT     - trade has filled in line items but not sent yet
  SENT      - emailed to customer with a payment link
  PARTIALLY_PAID - some payment received but balance > 0
  PAID      - balance == 0
  WRITTEN_OFF    - trade has given up on collection (manual)
  VOID      - trade cancelled before sending

Money is stored in pence (integers). The deposit_pence field captures
how much the customer already paid at booking time, so the invoice
shows "amount due = total - deposit".
"""
from __future__ import annotations

import enum
from datetime import datetime, date

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models._base import IdMixin, TimestampMixin


class InvoiceStatus(str, enum.Enum):
    DRAFT = "draft"
    SENT = "sent"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    WRITTEN_OFF = "written_off"
    VOID = "void"


# Statuses where the customer still owes money — used to filter "money owed" view.
OUTSTANDING_INVOICE_STATUSES = {
    InvoiceStatus.SENT,
    InvoiceStatus.PARTIALLY_PAID,
}


class Invoice(IdMixin, TimestampMixin, db.Model):
    __tablename__ = "invoices"

    # Links
    booking_id: Mapped[int] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trade_id: Mapped[int] = mapped_column(
        ForeignKey("trades.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Identity
    # Format: INV-MCK-2025-0001 (3-letter prefix from trade slug + year + sequence)
    number: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)

    # Status
    status: Mapped[InvoiceStatus] = mapped_column(
        db.Enum(
            InvoiceStatus,
            name="invoice_status",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        default=InvoiceStatus.DRAFT,
        nullable=False,
        index=True,
    )

    # Money (all pence, GBP unless we go multi-currency)
    subtotal_pence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vat_pence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_pence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deposit_pence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        # how much the customer paid at booking time, deducted from amount due
    )
    amount_paid_pence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")

    # VAT — flagged at invoice level; many small NI trades aren't VAT registered
    is_vat_registered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    vat_number: Mapped[str | None] = mapped_column(String(40))
    vat_rate_bps: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2000,
        # 2000 basis points = 20% (UK standard rate)
    )

    # Dates
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_date: Mapped[date | None] = mapped_column(Date)  # 14d post-issue by default
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Free text
    notes: Mapped[str | None] = mapped_column(Text)
    # Internal-only — trade's notes about the invoice, never shown to customer
    internal_notes: Mapped[str | None] = mapped_column(Text)

    # Stripe
    stripe_payment_link_id: Mapped[str | None] = mapped_column(String(200))
    stripe_payment_link_url: Mapped[str | None] = mapped_column(String(500))

    # Chase tracking — last reminder sent to avoid double-sending
    last_chase_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    chase_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Relationships
    booking = relationship("Booking", back_populates="invoices")
    trade = relationship("Trade")
    line_items = relationship(
        "InvoiceLineItem",
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceLineItem.position",
    )

    __table_args__ = (
        Index("ix_invoices_trade_status", "trade_id", "status"),
        Index("ix_invoices_due_date", "due_date"),
    )

    @property
    def amount_due_pence(self) -> int:
        """How much the customer still owes."""
        return max(self.total_pence - self.deposit_pence - self.amount_paid_pence, 0)

    @property
    def is_outstanding(self) -> bool:
        return self.status in OUTSTANDING_INVOICE_STATUSES

    @property
    def is_overdue(self) -> bool:
        if not self.is_outstanding or not self.due_date:
            return False
        return self.due_date < date.today()

    @property
    def days_overdue(self) -> int:
        if not self.is_overdue:
            return 0
        return (date.today() - self.due_date).days

    def __repr__(self) -> str:
        return f"<Invoice {self.number} {self.status.value}>"


class InvoiceLineItem(IdMixin, db.Model):
    __tablename__ = "invoice_line_items"

    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity_milli: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1000,
        # 1.0 = 1000. Allows fractional hours (e.g. 1.5h = 1500) without floats.
    )
    unit_price_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    total_pence: Mapped[int] = mapped_column(Integer, nullable=False)

    invoice = relationship("Invoice", back_populates="line_items")
