"""Invoice routes for the dashboard.

Trade can:
  - List outstanding invoices (the 'Money owed' view)
  - Create an invoice from a completed booking
  - Send / void / write off invoices
  - Download invoice PDF
"""
from __future__ import annotations

import logging

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required
from io import BytesIO
from sqlalchemy import desc, select

from app.dashboard.invoice_forms import (
    CreateInvoiceForm,
    VoidInvoiceForm,
    WriteOffForm,
)
from app.extensions import db
from app.models import Booking, BookingStatus, Invoice, InvoiceStatus
from app.services import invoices as invoice_svc
from app.services.errors import DomainError
from app.services.invoice_pdf import render_invoice_pdf

logger = logging.getLogger(__name__)

bp = Blueprint("invoices", __name__, template_folder="../templates/dashboard")


def _parse_pence(text: str) -> int:
    """Turn '£45.50' or '45' or '45.5' into pence (4550, 4500, 4550)."""
    if text is None:
        raise ValueError("missing")
    cleaned = text.strip().replace("£", "").replace(",", "")
    if not cleaned:
        raise ValueError("empty")
    pence = round(float(cleaned) * 100)
    if pence < 0:
        raise ValueError("negative")
    return pence


def _parse_quantity_milli(text: str) -> int:
    """'1.5' -> 1500, '2' -> 2000, '1h' -> 1000."""
    if text is None or not text.strip():
        return 1000
    cleaned = text.strip().lower().rstrip("h").rstrip(" ").strip()
    return round(float(cleaned) * 1000)


def _trade_invoice_or_404(invoice_id: int) -> Invoice:
    inv = db.session.get(Invoice, invoice_id)
    if not inv or inv.trade_id != current_user.id:
        abort(404)
    return inv


@bp.before_request
@login_required
def _require_login():
    pass


@bp.route("/")
def index():
    """List the trade's invoices, grouped by status."""
    outstanding = list(
        db.session.execute(
            select(Invoice)
            .where(
                Invoice.trade_id == current_user.id,
                Invoice.status.in_([
                    InvoiceStatus.SENT.value,
                    InvoiceStatus.PARTIALLY_PAID.value,
                ]),
            )
            .order_by(Invoice.due_date.asc())
        ).scalars()
    )
    drafts = list(
        db.session.execute(
            select(Invoice)
            .where(
                Invoice.trade_id == current_user.id,
                Invoice.status == InvoiceStatus.DRAFT.value,
            )
            .order_by(desc(Invoice.created_at))
        ).scalars()
    )
    paid_recent = list(
        db.session.execute(
            select(Invoice)
            .where(
                Invoice.trade_id == current_user.id,
                Invoice.status == InvoiceStatus.PAID.value,
            )
            .order_by(desc(Invoice.paid_at))
            .limit(20)
        ).scalars()
    )

    total_outstanding = sum(i.amount_due_pence for i in outstanding)

    return render_template(
        "dashboard/invoices_index.html",
        outstanding=outstanding,
        drafts=drafts,
        paid_recent=paid_recent,
        total_outstanding=total_outstanding,
    )


@bp.route("/new/<int:booking_id>", methods=["GET", "POST"])
def new(booking_id: int):
    """Create a draft invoice from a completed booking."""
    booking = db.session.get(Booking, booking_id)
    if not booking or booking.trade_id != current_user.id:
        abort(404)
    if booking.status != BookingStatus.COMPLETE:
        flash("You can only invoice completed bookings.", "error")
        return redirect(url_for("dashboard.booking_detail", booking_id=booking.id))

    # Already has an active invoice?
    existing = next(
        (i for i in booking.invoices if i.status not in (InvoiceStatus.VOID,)),
        None,
    )
    if existing:
        flash(f"Invoice {existing.number} already exists for this booking.", "info")
        return redirect(url_for("invoices.show", invoice_id=existing.id))

    form = CreateInvoiceForm(booking_id=str(booking.id))

    if request.method == "POST" and form.validate_on_submit():
        # Parse line items from request.form
        try:
            line_items = _extract_line_items(request.form)
        except ValueError as e:
            flash(f"Couldn't parse line items: {e}", "error")
            return render_template(
                "dashboard/invoice_new.html", form=form, booking=booking,
                line_items=_default_line_items(booking),
            )

        if not line_items:
            flash("Add at least one line item.", "error")
            return render_template(
                "dashboard/invoice_new.html", form=form, booking=booking,
                line_items=_default_line_items(booking),
            )

        try:
            invoice = invoice_svc.create_invoice_from_booking(
                booking=booking,
                line_items=line_items,
                notes=form.notes.data or None,
                is_vat_registered=bool(form.is_vat_registered.data),
                vat_number=form.vat_number.data or None,
                vat_rate_bps=int(form.vat_rate.data or 20) * 100,
                payment_terms_days=int(form.payment_terms_days.data or 14),
            )
        except DomainError as e:
            flash(str(e), "error")
            return render_template(
                "dashboard/invoice_new.html", form=form, booking=booking,
                line_items=_default_line_items(booking),
            )

        flash(f"Invoice {invoice.number} drafted. Review and send.", "success")
        return redirect(url_for("invoices.show", invoice_id=invoice.id))

    return render_template(
        "dashboard/invoice_new.html",
        form=form,
        booking=booking,
        line_items=_default_line_items(booking),
    )


def _default_line_items(booking: Booking) -> list[dict]:
    """Pre-fill with the booked service as the first line."""
    return [
        {
            "description": booking.service.name if booking.service else "Service rendered",
            "quantity": "1",
            "unit_price": "",
        }
    ]


def _extract_line_items(form_data) -> list[dict]:
    """Pick out line items from form using li-d-N / li-q-N / li-p-N keys."""
    items = []
    indices = sorted({
        int(k.split("-")[-1])
        for k in form_data.keys()
        if k.startswith(("li-d-", "li-q-", "li-p-"))
        and k.split("-")[-1].isdigit()
    })
    for idx in indices:
        desc = (form_data.get(f"li-d-{idx}") or "").strip()
        qty = (form_data.get(f"li-q-{idx}") or "1").strip()
        price_text = (form_data.get(f"li-p-{idx}") or "").strip()
        if not desc:
            continue
        if not price_text:
            raise ValueError(f"Line {idx + 1}: missing unit price")
        try:
            unit_price = _parse_pence(price_text)
            qty_milli = _parse_quantity_milli(qty)
        except (ValueError, TypeError) as e:
            raise ValueError(f"Line {idx + 1}: {e}") from e

        items.append({
            "description": desc,
            "quantity_milli": qty_milli,
            "unit_price_pence": unit_price,
        })
    return items


@bp.route("/<int:invoice_id>")
def show(invoice_id: int):
    invoice = _trade_invoice_or_404(invoice_id)
    return render_template("dashboard/invoice_detail.html", invoice=invoice)


@bp.route("/<int:invoice_id>/send", methods=["POST"])
def send(invoice_id: int):
    invoice = _trade_invoice_or_404(invoice_id)
    try:
        invoice_svc.send_invoice(invoice)
        flash(f"Invoice {invoice.number} sent to {invoice.booking.customer_email}.", "success")
    except DomainError as e:
        flash(str(e), "error")
    return redirect(url_for("invoices.show", invoice_id=invoice.id))


@bp.route("/<int:invoice_id>/resend", methods=["POST"])
def resend(invoice_id: int):
    """Re-send an already-sent invoice (e.g., customer lost it)."""
    invoice = _trade_invoice_or_404(invoice_id)
    if invoice.status not in (InvoiceStatus.SENT, InvoiceStatus.PARTIALLY_PAID):
        flash("Can only resend sent or partially-paid invoices.", "error")
        return redirect(url_for("invoices.show", invoice_id=invoice.id))

    from app.services import email as email_svc
    email_svc.send_email(
        to=invoice.booking.customer_email,
        subject=f"Your invoice from {invoice.trade.business_name}",
        template="emails/invoice_sent",
        invoice=invoice,
        booking=invoice.booking,
        trade=invoice.trade,
    )
    flash("Invoice resent.", "success")
    return redirect(url_for("invoices.show", invoice_id=invoice.id))


@bp.route("/<int:invoice_id>/void", methods=["POST"])
def void(invoice_id: int):
    invoice = _trade_invoice_or_404(invoice_id)
    form = VoidInvoiceForm()
    try:
        invoice_svc.void_invoice(invoice, reason=form.reason.data)
        flash(f"Invoice {invoice.number} voided.", "info")
    except DomainError as e:
        flash(str(e), "error")
    return redirect(url_for("invoices.show", invoice_id=invoice.id))


@bp.route("/<int:invoice_id>/write-off", methods=["POST"])
def write_off(invoice_id: int):
    invoice = _trade_invoice_or_404(invoice_id)
    form = WriteOffForm()
    try:
        invoice_svc.write_off_invoice(invoice, reason=form.reason.data)
        flash(f"Invoice {invoice.number} marked as written off.", "info")
    except DomainError as e:
        flash(str(e), "error")
    return redirect(url_for("invoices.show", invoice_id=invoice.id))


@bp.route("/<int:invoice_id>/mark-paid", methods=["POST"])
def mark_paid(invoice_id: int):
    """Manually record an out-of-band payment (cash, bank transfer)."""
    invoice = _trade_invoice_or_404(invoice_id)
    amount_text = (request.form.get("amount") or "").strip()
    try:
        amount_pence = _parse_pence(amount_text)
    except ValueError:
        flash("Enter a valid amount.", "error")
        return redirect(url_for("invoices.show", invoice_id=invoice.id))

    if amount_pence <= 0:
        flash("Amount must be greater than zero.", "error")
        return redirect(url_for("invoices.show", invoice_id=invoice.id))

    invoice_svc.mark_invoice_paid(invoice, amount_pence)
    flash(f"Payment of £{amount_pence/100:.2f} recorded.", "success")
    return redirect(url_for("invoices.show", invoice_id=invoice.id))


@bp.route("/<int:invoice_id>.pdf")
def download_pdf(invoice_id: int):
    invoice = _trade_invoice_or_404(invoice_id)
    pdf_bytes = render_invoice_pdf(invoice)
    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"{invoice.number}.pdf",
    )
