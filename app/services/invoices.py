"""Invoice service — create, send, mark paid, chase.

Invoices live on top of bookings. A booking is invoiced after it's marked
COMPLETE. The deposit already paid is deducted from the invoice total, so
the customer only needs to pay the balance.
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, func, select

from app.extensions import db
from app.models import (
    Booking,
    BookingStatus,
    Invoice,
    InvoiceLineItem,
    InvoiceStatus,
    OUTSTANDING_INVOICE_STATUSES,
    Trade,
)
from app.services.errors import DomainError, InvalidTransition

logger = logging.getLogger(__name__)


# Default invoice payment terms — 14 days. Override per-trade in V2.
DEFAULT_PAYMENT_TERMS_DAYS = 14

# Auto-chase schedule (days after due date when reminders are sent)
CHASE_SCHEDULE_DAYS = [7, 14, 30]


def generate_invoice_number(trade: Trade) -> str:
    """Build a unique invoice number like 'INV-MCK-2025-0001'.

    Sequence is per-trade per-year so trades' invoices are easy to track.
    """
    year = datetime.now(UTC).year
    prefix = trade.slug[:3].upper().ljust(3, "X")

    # Find the highest sequence for this trade and year
    pattern = f"INV-{prefix}-{year}-%"
    last = db.session.execute(
        select(func.max(Invoice.number)).where(Invoice.number.like(pattern))
    ).scalar()
    if last:
        seq = int(last.split("-")[-1]) + 1
    else:
        seq = 1
    return f"INV-{prefix}-{year}-{seq:04d}"


def create_invoice_from_booking(
    booking: Booking,
    line_items: list[dict],
    notes: str | None = None,
    is_vat_registered: bool = False,
    vat_number: str | None = None,
    vat_rate_bps: int = 2000,
    payment_terms_days: int = DEFAULT_PAYMENT_TERMS_DAYS,
) -> Invoice:
    """Create a draft invoice from a completed booking.

    line_items: list of dicts with keys:
      - description (str)
      - quantity_milli (int, default 1000 = 1.0)
      - unit_price_pence (int)

    The deposit already paid (booking.deposit_pence if PAID) is captured on
    the invoice and shown as a credit, so customer pays balance only.
    """
    if booking.status != BookingStatus.COMPLETE:
        raise InvalidTransition(
            f"Can only invoice completed bookings (status: {booking.status.value})"
        )

    if not line_items:
        raise DomainError("Invoice must have at least one line item")

    trade = booking.trade

    # Build line items, calculate totals
    items_obj: list[InvoiceLineItem] = []
    subtotal_pence = 0
    for i, item in enumerate(line_items):
        qty_milli = int(item.get("quantity_milli", 1000))
        unit_price = int(item["unit_price_pence"])
        line_total = round(qty_milli * unit_price / 1000)

        items_obj.append(
            InvoiceLineItem(
                position=i,
                description=item["description"][:255],
                quantity_milli=qty_milli,
                unit_price_pence=unit_price,
                total_pence=line_total,
            )
        )
        subtotal_pence += line_total

    # VAT calculation — only if trade is registered
    if is_vat_registered:
        vat_pence = round(subtotal_pence * vat_rate_bps / 10000)
    else:
        vat_pence = 0
    total_pence = subtotal_pence + vat_pence

    # Deposit credit — only if it was actually paid
    deposit_paid = booking.deposit_pence if booking.payment_status.value == "paid" else 0

    invoice = Invoice(
        booking_id=booking.id,
        trade_id=trade.id,
        number=generate_invoice_number(trade),
        status=InvoiceStatus.DRAFT,
        subtotal_pence=subtotal_pence,
        vat_pence=vat_pence,
        total_pence=total_pence,
        deposit_pence=deposit_paid,
        amount_paid_pence=0,
        currency="GBP",
        is_vat_registered=is_vat_registered,
        vat_number=vat_number,
        vat_rate_bps=vat_rate_bps,
        notes=notes,
        due_date=date.today() + timedelta(days=payment_terms_days),
    )
    invoice.line_items = items_obj

    db.session.add(invoice)
    db.session.commit()
    return invoice


def send_invoice(invoice: Invoice) -> Invoice:
    """Move invoice from DRAFT to SENT and create a Stripe payment link.

    Sends the customer email with a link to view+pay.
    """
    from app.services import email as email_svc
    from app.services.invoice_payments import create_invoice_payment_link

    if invoice.status != InvoiceStatus.DRAFT:
        raise InvalidTransition(
            f"Can only send draft invoices (status: {invoice.status.value})"
        )

    # Create Stripe Payment Link if we don't have one yet
    if invoice.amount_due_pence > 0 and not invoice.stripe_payment_link_url:
        try:
            link = create_invoice_payment_link(invoice)
            invoice.stripe_payment_link_id = link.get("id")
            invoice.stripe_payment_link_url = link.get("url")
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to create Stripe payment link for %s: %s", invoice.number, e)
            # Continue without payment link — trade can resend later.

    invoice.status = InvoiceStatus.SENT
    invoice.issued_at = datetime.now(UTC)
    db.session.commit()

    # Send email
    booking = invoice.booking
    email_svc.send_email(
        to=booking.customer_email,
        subject=f"Your invoice from {invoice.trade.business_name}",
        template="invoice_sent",
        invoice=invoice,
        booking=booking,
        trade=invoice.trade,
    )

    return invoice


def mark_invoice_paid(
    invoice: Invoice, amount_pence: int, stripe_charge_id: str | None = None
) -> Invoice:
    """Record a payment against an invoice.

    amount_pence > full balance: clamps to balance (overpayment ignored).
    amount_pence == full balance: PAID.
    amount_pence < full balance: PARTIALLY_PAID.
    """
    if invoice.status not in (InvoiceStatus.SENT, InvoiceStatus.PARTIALLY_PAID):
        # Already PAID, WRITTEN_OFF, VOID, or DRAFT — refuse silently.
        logger.warning(
            "Refusing to mark_paid invoice %s in status %s",
            invoice.number,
            invoice.status.value,
        )
        return invoice

    balance = invoice.amount_due_pence
    actual = min(amount_pence, balance)
    invoice.amount_paid_pence += actual

    if invoice.amount_due_pence <= 0:
        invoice.status = InvoiceStatus.PAID
        invoice.paid_at = datetime.now(UTC)
    else:
        invoice.status = InvoiceStatus.PARTIALLY_PAID

    db.session.commit()
    return invoice


def void_invoice(invoice: Invoice, reason: str | None = None) -> Invoice:
    """Cancel an invoice. Only allowed if not yet paid."""
    if invoice.status == InvoiceStatus.PAID:
        raise InvalidTransition("Can't void a paid invoice — issue a refund instead.")
    if invoice.amount_paid_pence > 0:
        raise InvalidTransition(
            "Can't void an invoice with payments — write off the balance instead."
        )
    invoice.status = InvoiceStatus.VOID
    if reason:
        notes = (invoice.internal_notes or "") + f"\n[VOID] {reason}"
        invoice.internal_notes = notes.strip()
    db.session.commit()
    return invoice


def write_off_invoice(invoice: Invoice, reason: str | None = None) -> Invoice:
    """Mark balance as written off (not collectable)."""
    if not invoice.is_outstanding:
        raise InvalidTransition(
            f"Can't write off invoice in status {invoice.status.value}"
        )
    invoice.status = InvoiceStatus.WRITTEN_OFF
    if reason:
        notes = (invoice.internal_notes or "") + f"\n[WRITTEN OFF] {reason}"
        invoice.internal_notes = notes.strip()
    db.session.commit()
    return invoice


# ---------- Auto-chase ----------


def find_invoices_due_for_chase() -> list[Invoice]:
    """Return outstanding invoices that should be chased today.

    Rules:
      - Must be SENT or PARTIALLY_PAID
      - Must be past due_date by at least 7 days
      - Must not have been chased in the last 7 days
      - Stop after CHASE_SCHEDULE_DAYS milestones (i.e. after ~30 days, manual)
    """
    today = date.today()
    seven_days_ago = datetime.now(UTC) - timedelta(days=7)

    stmt = select(Invoice).where(
        Invoice.status.in_([InvoiceStatus.SENT.value, InvoiceStatus.PARTIALLY_PAID.value]),
        Invoice.due_date.isnot(None),
        Invoice.due_date < today,
        Invoice.chase_count < len(CHASE_SCHEDULE_DAYS),
    )
    candidates = list(db.session.execute(stmt).scalars())

    out = []
    for inv in candidates:
        days_overdue = (today - inv.due_date).days
        # Find the next milestone we haven't chased on yet
        milestone = CHASE_SCHEDULE_DAYS[inv.chase_count] if inv.chase_count < len(CHASE_SCHEDULE_DAYS) else None
        if milestone is None:
            continue
        if days_overdue < milestone:
            continue
        # Don't chase twice within 7d
        if inv.last_chase_at and inv.last_chase_at > seven_days_ago:
            continue
        out.append(inv)
    return out


def chase_invoice(invoice: Invoice) -> None:
    """Send a payment reminder email to the customer."""
    from app.services import email as email_svc

    booking = invoice.booking

    if invoice.chase_count == 0:
        tone = "friendly"
        subject = f"Friendly reminder: invoice {invoice.number}"
    elif invoice.chase_count == 1:
        tone = "firm"
        subject = f"Second reminder: invoice {invoice.number} is now overdue"
    else:
        tone = "final"
        subject = f"Final reminder: invoice {invoice.number}"

    email_svc.send_email(
        to=booking.customer_email,
        subject=subject,
        template="invoice_reminder",
        invoice=invoice,
        booking=booking,
        trade=invoice.trade,
        tone=tone,
        days_overdue=invoice.days_overdue,
    )

    invoice.last_chase_at = datetime.now(UTC)
    invoice.chase_count += 1
    db.session.commit()


# ---------- Lookups ----------


def find_invoice_by_number(number: str) -> Invoice | None:
    return db.session.execute(
        select(Invoice).where(Invoice.number == number)
    ).scalar_one_or_none()


def get_outstanding_total_pence(trade_id: int) -> int:
    """Sum of all amount_due across outstanding invoices for a trade.

    Used for the 'Money owed' headline on the dashboard.
    """
    invoices = db.session.execute(
        select(Invoice).where(
            and_(
                Invoice.trade_id == trade_id,
                Invoice.status.in_([s.value for s in OUTSTANDING_INVOICE_STATUSES]),
            )
        )
    ).scalars()
    return sum(inv.amount_due_pence for inv in invoices)
