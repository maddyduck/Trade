"""Stripe payment links for invoices.

Distinct from the checkout flow used at booking time:
  - Booking checkout: customer pays deposit_pence, money to trade.
  - Invoice payment: customer pays invoice balance, money to trade.

We use Stripe Payment Links (one-shot products) because:
  - No need for a hosted checkout page on our side
  - Customer can pay from any device, no login
  - Stripe handles 3DS, receipts, and tax forms
  - We get notified via webhook on completion
"""
from __future__ import annotations

import logging

import stripe
from flask import current_app

from app.models import Invoice
from app.services.errors import DomainError

logger = logging.getLogger(__name__)


class InvoicePaymentLinkError(DomainError):
    pass


def create_invoice_payment_link(invoice: Invoice) -> dict:
    """Create a Stripe Payment Link routed to the trade's connected account.

    Returns the raw Stripe response dict.
    """
    trade = invoice.trade
    if not trade.stripe_account_id:
        raise InvoicePaymentLinkError(
            "Trade hasn't connected Stripe yet — can't create payment link."
        )
    if not trade.stripe_charges_enabled:
        raise InvoicePaymentLinkError(
            "Trade's Stripe account isn't fully set up — can't create payment link."
        )

    amount = invoice.amount_due_pence
    if amount <= 0:
        raise InvoicePaymentLinkError("Invoice has no balance to pay")

    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]

    # First create the price/product on the trade's account
    try:
        product = stripe.Product.create(
            name=f"{trade.business_name} — Invoice {invoice.number}",
            stripe_account=trade.stripe_account_id,
            idempotency_key=f"invoice-product:{invoice.number}",
        )
        price = stripe.Price.create(
            product=product.id,
            unit_amount=amount,
            currency=invoice.currency.lower(),
            stripe_account=trade.stripe_account_id,
            idempotency_key=f"invoice-price:{invoice.number}",
        )
        link = stripe.PaymentLink.create(
            line_items=[{"price": price.id, "quantity": 1}],
            metadata={
                "invoice_number": invoice.number,
                "invoice_id": str(invoice.id),
                "booking_reference": invoice.booking.reference,
            },
            after_completion={
                "type": "redirect",
                "redirect": {
                    "url": _completion_url(invoice),
                },
            },
            stripe_account=trade.stripe_account_id,
            idempotency_key=f"invoice-link:{invoice.number}",
        )
        return dict(link)
    except stripe.error.StripeError as e:
        logger.exception("Stripe error creating payment link for %s", invoice.number)
        raise InvoicePaymentLinkError(str(e)) from e


def _completion_url(invoice: Invoice) -> str:
    """Where customer is redirected after paying the invoice."""
    base = current_app.config.get("APP_BASE_URL", "http://localhost:5000")
    return f"{base}/invoices/{invoice.number}/paid"
