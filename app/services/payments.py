"""Stripe integration — hardened.

Pattern: Destination charges via Stripe Connect Express.
  - Platform is merchant of record for the Checkout session.
  - Funds are transferred to the connected account automatically.
  - Refunds and disputes flow through the platform first, simplifying ops.
  - Stripe fees come out of the destination (the trade), visible on payout.

Why destination charges (vs separate charges and transfers):
  - Simpler: one API call per payment, one refund flow.
  - Better UX: customer sees the trade's name on their statement via
    `statement_descriptor_suffix` (Stripe truncates to 22 chars after prefix).
  - Disputes: we control the response, which matters for deposits.
  Trade-off: platform fee is optional; MVP uses 0.

Webhook events we care about:
  checkout.session.completed        → flip PENDING_PAYMENT → BOOKED, email
  payment_intent.payment_failed     → record failure
  charge.refunded                   → update payment_status
  account.updated                   → refresh trade's charges_enabled flags

Idempotency strategy:
  - Checkout sessions: reuse an open session on repeat POSTs (by looking up
    `stripe_checkout_session_id` on the booking) and send Stripe an
    idempotency key so Stripe-level duplicates are deduped too.
  - Webhooks: every event id is logged in StripeEvent, duplicates skipped.
  - mark_paid: refuses to promote a CANCELLED booking back to BOOKED
    (guards the race where customer cancels just as Stripe confirms).
  - refund_booking: idempotency key derived from booking reference.

Reconciliation: if a webhook is missed, reconcile_booking(booking) pulls
state from Stripe and updates locally. Called from /booked/<ref> as a safety
net and exposed as `flask reconcile <ref>`.
"""
from __future__ import annotations

import logging
from typing import Any

import stripe
from flask import current_app, url_for

from app.extensions import db
from app.models import Booking, PaymentStatus, Trade

logger = logging.getLogger(__name__)


# --- Typed exceptions (caught by views) -----------------------------------


class PaymentsError(Exception):
    """Base for payment-layer errors safe to show a user-facing message for."""

    def __init__(self, message: str, *, code: str = "payments_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class TradeStripeNotReady(PaymentsError):
    """The trade's Stripe account can't accept charges right now."""


class CheckoutCreationFailed(PaymentsError):
    pass


class RefundFailed(PaymentsError):
    pass


# --- Init ------------------------------------------------------------------


def _init_stripe() -> None:
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]
    stripe.api_version = "2024-06-20"


# --- Connect onboarding ----------------------------------------------------


def create_account_link(trade: Trade) -> str:
    """Create or reuse a connected Express account; return onboarding URL."""
    _init_stripe()

    if not trade.stripe_account_id:
        account = stripe.Account.create(
            type="express",
            country="GB",
            email=trade.email,
            capabilities={
                "card_payments": {"requested": True},
                "transfers": {"requested": True},
            },
            business_type="individual",
            metadata={"trade_id": str(trade.id), "slug": trade.slug},
        )
        trade.stripe_account_id = account.id
        db.session.commit()

    link = stripe.AccountLink.create(
        account=trade.stripe_account_id,
        refresh_url=url_for("dashboard.stripe_connect", _external=True),
        return_url=url_for("dashboard.stripe_return", _external=True),
        type="account_onboarding",
    )
    return link.url


def refresh_trade_account_status(trade: Trade) -> None:
    """Pull latest capability flags from Stripe onto the Trade row."""
    if not trade.stripe_account_id:
        return
    _init_stripe()
    account = stripe.Account.retrieve(trade.stripe_account_id)
    trade.stripe_charges_enabled = bool(account.charges_enabled)
    trade.stripe_payouts_enabled = bool(account.payouts_enabled)


# --- Checkout session creation (idempotent) --------------------------------


def create_checkout_session(booking: Booking) -> str:
    """Return the Stripe-hosted Checkout URL for the deposit payment.

    Idempotent by design:
      - If the booking already has a non-expired checkout session, reuse it.
      - Stripe idempotency-key on the create call uses the booking reference,
        so two requests in flight resolve to the same session.
    """
    _init_stripe()

    trade = booking.trade
    service = booking.service

    if not trade.stripe_account_id or not trade.stripe_charges_enabled:
        raise TradeStripeNotReady(
            "This trade isn't set up to accept deposits right now."
        )

    # --- Reuse existing open session if any -------------------------------
    if booking.stripe_checkout_session_id:
        try:
            existing = stripe.checkout.Session.retrieve(
                booking.stripe_checkout_session_id
            )
            if existing.status == "open" and existing.url:
                logger.info(
                    "Reusing open Checkout session %s for booking %s",
                    existing.id,
                    booking.reference,
                )
                return existing.url
            if existing.status == "complete":
                return url_for(
                    "public.stripe_return",
                    slug=trade.slug,
                    ref=booking.reference,
                    status="success",
                    _external=True,
                )
            # `expired` → fall through to create a fresh session.
        except stripe.error.StripeError:
            logger.warning(
                "Couldn't retrieve session %s — creating new one",
                booking.stripe_checkout_session_id,
            )

    # --- Build new session -------------------------------------------------
    base = current_app.config["APP_BASE_URL"].rstrip("/")
    success_url = base + url_for(
        "public.stripe_return",
        slug=trade.slug,
        ref=booking.reference,
        status="success",
    )
    cancel_url = base + url_for(
        "public.stripe_return",
        slug=trade.slug,
        ref=booking.reference,
        status="cancel",
    )

    platform_fee_pct = current_app.config.get("PLATFORM_FEE_PCT", 0)
    application_fee_amount = (booking.deposit_pence * platform_fee_pct) // 100

    suffix = _statement_suffix(trade.business_name)

    try:
        session = stripe.checkout.Session.create(
            idempotency_key=f"checkout:{booking.reference}",
            mode="payment",
            success_url=success_url,
            cancel_url=cancel_url,
            customer_email=booking.customer_email,
            client_reference_id=booking.reference,
            line_items=[
                {
                    "price_data": {
                        "currency": booking.currency.lower(),
                        "unit_amount": booking.deposit_pence,
                        "product_data": {
                            "name": f"{service.name} — deposit",
                            "description": (
                                f"Deposit to secure {service.name.lower()} "
                                f"with {trade.business_name} "
                                f"on {booking.start_at.strftime('%a %d %b at %H:%M')}."
                            ),
                        },
                    },
                    "quantity": 1,
                }
            ],
            payment_intent_data={
                "application_fee_amount": application_fee_amount,
                "transfer_data": {"destination": trade.stripe_account_id},
                "statement_descriptor_suffix": suffix,
                "metadata": {
                    "booking_id": str(booking.id),
                    "reference": booking.reference,
                    "trade_id": str(trade.id),
                },
            },
            metadata={
                "booking_id": str(booking.id),
                "reference": booking.reference,
                "trade_id": str(trade.id),
            },
        )
    except stripe.error.StripeError as e:
        logger.exception("Stripe rejected Checkout session creation")
        raise CheckoutCreationFailed(
            "We couldn't start the payment. Please try again in a moment."
        ) from e

    booking.stripe_checkout_session_id = session.id
    db.session.commit()

    return session.url


def _statement_suffix(business_name: str) -> str:
    """Stripe allows letters/digits/spaces, truncates overall to 22 chars."""
    cleaned = "".join(
        c for c in business_name.upper().replace("&", "AND") if c.isalnum() or c == " "
    ).strip()
    return cleaned[:12] or "SORTED"


# --- Refunds ---------------------------------------------------------------


def refund_booking(
    booking: Booking, amount_pence: int, *, reason: str = "requested_by_customer"
) -> str | None:
    """Issue a refund. Returns refund id, or None if no refund was made.

    Idempotent via Stripe idempotency key keyed off booking reference +
    amount, so retries for the same refund don't double-charge.

    Stripe's minimum refund is 1 pence; 0 silently returns None.
    """
    if amount_pence <= 0 or not booking.stripe_payment_intent_id:
        return None
    _init_stripe()

    try:
        refund = stripe.Refund.create(
            idempotency_key=f"refund:{booking.reference}:{amount_pence}",
            payment_intent=booking.stripe_payment_intent_id,
            amount=amount_pence,
            reason=reason,
            reverse_transfer=True,
            metadata={
                "booking_id": str(booking.id),
                "reference": booking.reference,
            },
        )
    except stripe.error.StripeError as e:
        logger.exception("Refund failed for booking %s", booking.reference)
        raise RefundFailed("We couldn't process the refund right now.") from e

    booking.stripe_refund_id = refund.id
    booking.payment_status = (
        PaymentStatus.REFUNDED
        if amount_pence >= booking.deposit_pence
        else PaymentStatus.PARTIAL_REFUND
    )
    db.session.commit()
    return refund.id


# --- Webhook verification --------------------------------------------------


def verify_webhook(payload: bytes, sig_header: str) -> dict[str, Any]:
    """Verify signature and return the Stripe event dict."""
    _init_stripe()
    secret = current_app.config["STRIPE_WEBHOOK_SECRET"]
    return stripe.Webhook.construct_event(payload, sig_header, secret)


# --- Reconciliation: the safety net ----------------------------------------


def reconcile_booking(booking: Booking) -> dict[str, Any]:
    """Re-derive payment state from Stripe and apply to the local booking.

    Called from:
      - /booked/<ref> page on every hit (cheap safety net if webhook late).
      - `flask reconcile <ref>` for manual recovery.
      - After a webhook outage: `flask reconcile-recent --hours 6`.

    Returns a dict describing what changed, for logging.
    """
    _init_stripe()
    changes: dict[str, Any] = {
        "reference": booking.reference,
        "before": {
            "status": booking.status.value,
            "payment_status": booking.payment_status.value,
        },
        "actions": [],
    }

    # 1) Resolve payment_intent. Prefer the one we already stored.
    pi_id = booking.stripe_payment_intent_id
    if not pi_id and booking.stripe_checkout_session_id:
        try:
            session = stripe.checkout.Session.retrieve(
                booking.stripe_checkout_session_id
            )
            pi_id = session.payment_intent
            if pi_id:
                booking.stripe_payment_intent_id = pi_id
                db.session.commit()
                changes["actions"].append("resolved_payment_intent_from_session")
        except stripe.error.StripeError:
            changes["actions"].append("checkout_session_retrieve_failed")

    if not pi_id:
        changes["actions"].append("no_payment_intent_known")
        return changes

    try:
        intent = stripe.PaymentIntent.retrieve(pi_id)
    except stripe.error.StripeError:
        changes["actions"].append("payment_intent_retrieve_failed")
        return changes

    from app.services import bookings as booking_svc

    # 2) Apply payment state.
    if intent.status == "succeeded" and booking.payment_status != PaymentStatus.PAID:
        booking_svc.mark_paid(
            booking,
            stripe_payment_intent_id=intent.id,
            stripe_charge_id=intent.latest_charge,
        )
        changes["actions"].append("marked_paid")
    elif intent.status == "canceled":
        changes["actions"].append("intent_canceled_noop")

    # 3) Check for refunds we don't know about.
    if intent.latest_charge:
        try:
            charge = stripe.Charge.retrieve(intent.latest_charge)
            total_refunded = charge.amount_refunded or 0
            if total_refunded > 0:
                if total_refunded >= booking.deposit_pence:
                    if booking.payment_status != PaymentStatus.REFUNDED:
                        booking.payment_status = PaymentStatus.REFUNDED
                        db.session.commit()
                        changes["actions"].append("marked_refunded")
                elif booking.payment_status != PaymentStatus.PARTIAL_REFUND:
                    booking.payment_status = PaymentStatus.PARTIAL_REFUND
                    db.session.commit()
                    changes["actions"].append("marked_partial_refund")
        except stripe.error.StripeError:
            changes["actions"].append("charge_retrieve_failed")

    changes["after"] = {
        "status": booking.status.value,
        "payment_status": booking.payment_status.value,
    }
    return changes
