"""Stripe webhook handler.

Principles:
  - Idempotent: Stripe retries events, so handlers must be safe to run twice.
    We record every event_id; duplicates skip straight to 200.
  - Defensive: unknown event types → 200 (we don't want Stripe retrying
    events we simply don't handle).
  - Logged: every event recorded in StripeEvent for audit.
  - Never crash: handler-level exception → log and return 200. If we 500,
    Stripe retries for days, which compounds pain.

Webhooks arrive at /webhooks/stripe. Blueprint is CSRF-exempt; signatures
are verified by payments.verify_webhook() against STRIPE_WEBHOOK_SECRET.
"""
from __future__ import annotations

import logging

from flask import Blueprint, abort, current_app, request
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    Booking,
    BookingStatus,
    PaymentStatus,
    StripeEvent,
    Trade,
)
from app.services import bookings as booking_svc
from app.services import payments

logger = logging.getLogger(__name__)

bp = Blueprint("webhooks", __name__)


@bp.route("/stripe", methods=["POST"])
def stripe_webhook():
    payload = request.get_data()
    sig = request.headers.get("Stripe-Signature", "")

    # 1) Verify signature.
    try:
        event = payments.verify_webhook(payload, sig)
    except ValueError:
        logger.warning("Stripe webhook: invalid payload")
        abort(400)
    except Exception:  # including stripe.error.SignatureVerificationError
        logger.warning("Stripe webhook: signature verification failed")
        abort(400)

    event_id = event.get("id")
    event_type = event.get("type", "unknown")
    data = event["data"]["object"]

    # 2) Record in our log. Unique index on event_id dedupes Stripe replays.
    if not _record_event(event_id, event_type):
        logger.info("Stripe webhook %s: duplicate event, skipping", event_id)
        return "", 200

    logger.info("Stripe webhook received: %s id=%s", event_type, event_id)

    # 3) Dispatch. Never raise to Stripe.
    try:
        if event_type == "checkout.session.completed":
            _handle_checkout_completed(data)
        elif event_type == "payment_intent.payment_failed":
            _handle_payment_failed(data)
        elif event_type == "charge.refunded":
            _handle_charge_refunded(data)
        elif event_type == "account.updated":
            _handle_account_updated(data)
        else:
            _mark_event(event_id, "skipped", f"Ignored event type {event_type}")
            return "", 200

        _mark_event(event_id, "handled")
    except Exception as e:
        current_app.logger.exception(
            "Stripe webhook handler crashed on %s (event %s)", event_type, event_id
        )
        _mark_event(event_id, "errored", str(e)[:450])

    return "", 200


# ---- Event log helpers ---------------------------------------------------


def _record_event(event_id: str, event_type: str) -> bool:
    """Insert a StripeEvent row. Returns True if new, False if duplicate."""
    if not event_id:
        return True  # malformed; let dispatch handle
    ev = StripeEvent(event_id=event_id, event_type=event_type)
    db.session.add(ev)
    try:
        db.session.commit()
        return True
    except IntegrityError:
        db.session.rollback()
        return False


def _mark_event(event_id: str | None, result: str, note: str | None = None) -> None:
    if not event_id:
        return
    ev = (
        db.session.query(StripeEvent)
        .filter(StripeEvent.event_id == event_id)
        .one_or_none()
    )
    if ev:
        ev.processing_result = result
        if note:
            ev.note = note
        db.session.commit()


# ---- Handlers ------------------------------------------------------------


def _handle_checkout_completed(session: dict) -> None:
    ref = session.get("client_reference_id")
    booking: Booking | None = None
    if ref:
        try:
            booking = booking_svc.find_by_reference(ref)
        except Exception:
            booking = None

    if not booking:
        booking = (
            db.session.query(Booking)
            .filter(Booking.stripe_checkout_session_id == session["id"])
            .one_or_none()
        )

    if not booking:
        logger.warning(
            "Webhook: no booking matches session %s (ref=%s)", session["id"], ref
        )
        return

    payment_intent_id = session.get("payment_intent")
    if not payment_intent_id:
        logger.warning("Webhook: session %s has no payment_intent", session["id"])
        return

    # Capture the payment locally. This is idempotent.
    was_cancelled_before = booking.status == BookingStatus.CANCELLED
    booking_svc.mark_paid(booking, stripe_payment_intent_id=payment_intent_id)

    # --- Race handler: paid-but-cancelled ---
    # If the booking was already cancelled when this webhook arrived, the
    # customer has paid but their slot is gone. Auto-refund the deposit.
    if was_cancelled_before and booking.payment_status == PaymentStatus.PAID:
        logger.warning(
            "Paid-but-cancelled race for %s; auto-refunding", booking.reference
        )
        try:
            payments.refund_booking(
                booking,
                booking.deposit_pence,
                reason="duplicate",
            )
        except payments.RefundFailed:
            current_app.logger.exception(
                "Auto-refund failed for paid-but-cancelled %s — manual follow-up",
                booking.reference,
            )
        return  # don't send confirmation emails for a cancelled booking

    # --- Happy path: confirmation emails ---
    try:
        from flask import url_for

        from app.services.email import send_email
        from app.services.magic_links import make_token

        token = make_token(booking.id)
        link = url_for("public.manage_booking", token=token, _external=True)

        send_email(
            to=booking.customer_email,
            subject=f"You're booked in — {booking.reference}",
            template="customer_confirmation",
            booking=booking,
            manage_link=link,
        )
        send_email(
            to=booking.trade.email,
            subject=(
                f"New booking — {booking.customer_name} on "
                f"{booking.start_at.strftime('%a %d %b')}"
            ),
            template="trade_new_booking",
            booking=booking,
        )
    except Exception:
        current_app.logger.exception(
            "Failed to send confirmation emails for %s", booking.reference
        )


def _handle_payment_failed(intent: dict) -> None:
    ref = (intent.get("metadata") or {}).get("reference")
    if not ref:
        return
    try:
        booking = booking_svc.find_by_reference(ref)
    except Exception:
        return
    booking.payment_status = PaymentStatus.FAILED
    db.session.commit()


def _handle_charge_refunded(charge: dict) -> None:
    pi = charge.get("payment_intent")
    if not pi:
        return
    booking = (
        db.session.query(Booking)
        .filter(Booking.stripe_payment_intent_id == pi)
        .one_or_none()
    )
    if not booking:
        return
    amount_refunded = charge.get("amount_refunded", 0)
    total = charge.get("amount", 0)
    booking.payment_status = (
        PaymentStatus.REFUNDED
        if amount_refunded >= total
        else PaymentStatus.PARTIAL_REFUND
    )
    db.session.commit()


def _handle_account_updated(account: dict) -> None:
    trade = (
        db.session.query(Trade)
        .filter(Trade.stripe_account_id == account["id"])
        .one_or_none()
    )
    if not trade:
        return
    previously_enabled = trade.stripe_charges_enabled
    trade.stripe_charges_enabled = bool(account.get("charges_enabled"))
    trade.stripe_payouts_enabled = bool(account.get("payouts_enabled"))
    db.session.commit()

    if previously_enabled and not trade.stripe_charges_enabled:
        # A trade just got their Stripe disabled. Log loudly — they can't
        # take bookings and probably don't know yet.
        current_app.logger.warning(
            "Trade %s (%s) had charges_enabled revoked by Stripe — "
            "cannot accept bookings",
            trade.slug,
            trade.id,
        )
        # TODO(ops): email the trade to tell them what happened.
