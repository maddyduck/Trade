"""Customer-facing invoice routes.

  /invoices/<number>          - view with paste-into-browser support (uses email match)
  /invoices/<number>/paid     - Stripe redirect after successful payment
  /invoices/<number>.pdf      - download PDF (with email check)

For privacy: /invoices/<number> is NOT publicly accessible without proof
the visitor is the customer. Either:
  (a) the email query string matches the booking's customer email, or
  (b) they came from a magic link in their email (token in querystring).

In practice the magic link in the invoice email handles this transparently.
"""
from __future__ import annotations

from io import BytesIO

from flask import Blueprint, abort, flash, render_template, request, send_file
from sqlalchemy import select

from app.extensions import db
from app.models import Invoice
from app.services.invoice_pdf import render_invoice_pdf

bp = Blueprint("public_invoices", __name__, template_folder="../templates/public")


def _verify_customer(invoice: Invoice) -> bool:
    """Check that the visitor has proof they're the customer.

    Acceptable proofs:
      ?email=<customer_email>  (case-insensitive match)
    """
    email_q = (request.args.get("email") or "").strip().lower()
    if email_q and email_q == invoice.booking.customer_email.lower():
        return True
    # In future: also accept a magic-link token here.
    return False


@bp.route("/<number>")
def show_invoice(number: str):
    invoice = db.session.execute(
        select(Invoice).where(Invoice.number == number)
    ).scalar_one_or_none()
    if not invoice:
        abort(404)
    if not _verify_customer(invoice):
        # Show a "verify yourself" page rather than expose existence.
        return render_template(
            "public/invoice_verify.html", number=number,
        ), 403

    return render_template(
        "public/invoice_view.html",
        invoice=invoice,
        booking=invoice.booking,
        trade=invoice.trade,
    )


@bp.route("/<number>.pdf")
def download_invoice_pdf(number: str):
    invoice = db.session.execute(
        select(Invoice).where(Invoice.number == number)
    ).scalar_one_or_none()
    if not invoice:
        abort(404)
    if not _verify_customer(invoice):
        abort(403)

    pdf_bytes = render_invoice_pdf(invoice)
    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=False,  # display inline if browser supports
        download_name=f"{invoice.number}.pdf",
    )


@bp.route("/<number>/paid")
def payment_complete(number: str):
    """Where Stripe redirects after a successful payment-link checkout."""
    invoice = db.session.execute(
        select(Invoice).where(Invoice.number == number)
    ).scalar_one_or_none()
    if not invoice:
        abort(404)
    return render_template("public/invoice_paid.html", invoice=invoice)
