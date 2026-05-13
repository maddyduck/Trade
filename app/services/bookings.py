"""Booking service layer.

All mutations go through here so:
  - transitions are validated,
  - events are written,
  - DB commits happen in one place per mutation.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    ACTIVE_BOOKING_STATUSES,
    Booking,
    BookingEvent,
    BookingStatus,
    PaymentStatus,
    Service,
    Trade,
)
from app.services.errors import (
    BookingNotFound,
    InvalidTransition,
    PolicyViolation,
    SlotUnavailable,
    TradeNotBookable,
)
from app.services.references import generate_reference

# Allowed state transitions. Keep in sync with status stepper in UI.
_ALLOWED: dict[BookingStatus, set[BookingStatus]] = {
    BookingStatus.PENDING_PAYMENT: {BookingStatus.BOOKED, BookingStatus.CANCELLED},
    BookingStatus.BOOKED: {
        BookingStatus.ON_THE_WAY,
        BookingStatus.CANCELLED,
        BookingStatus.NO_SHOW,
    },
    BookingStatus.ON_THE_WAY: {BookingStatus.ARRIVED, BookingStatus.CANCELLED},
    BookingStatus.ARRIVED: {BookingStatus.IN_PROGRESS, BookingStatus.CANCELLED},
    BookingStatus.IN_PROGRESS: {BookingStatus.COMPLETE, BookingStatus.CANCELLED},
    BookingStatus.COMPLETE: set(),
    BookingStatus.CANCELLED: set(),
    BookingStatus.NO_SHOW: set(),
}


def _log_event(
    booking: Booking,
    event_type: str,
    *,
    actor: str,
    description: str | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
    ip: str | None = None,
) -> BookingEvent:
    ev = BookingEvent(
        booking_id=booking.id,
        event_type=event_type,
        actor=actor,
        description=description,
        from_status=from_status,
        to_status=to_status,
        ip_address=ip,
    )
    db.session.add(ev)
    return ev


def create_pending_booking(
    *,
    trade: Trade,
    service: Service,
    start_at_utc: datetime,
    customer_name: str,
    customer_phone: str,
    customer_email: str,
    customer_postcode: str,
    customer_address: str | None,
    job_notes: str | None,
    photo_urls: list[str] | None = None,
    ip: str | None = None,
) -> Booking:
    """Create a booking in PENDING_PAYMENT state.

    Checks availability at write-time (DB unique index is the ultimate arbiter)
    and raises SlotUnavailable on conflict.
    """
    # Allow booking if stripe is set up. Public page visibility is enforced in
    # the view layer by the trade_page/book_* routes requiring is_published.
    if not trade.stripe_charges_enabled:
        raise TradeNotBookable("This trade isn't set up to accept bookings yet.")

    end_at_utc = start_at_utc + timedelta(
        minutes=service.duration_minutes + trade.buffer_minutes
    )

    # Pre-check collision (cheap, not authoritative — the DB index is).
    conflict = db.session.execute(
        select(Booking.id).where(
            Booking.trade_id == trade.id,
            Booking.status.in_(ACTIVE_BOOKING_STATUSES),
            Booking.end_at > start_at_utc,
            Booking.start_at < end_at_utc,
        )
    ).first()
    if conflict:
        raise SlotUnavailable("That slot has just been taken — please pick another.")

    booking = Booking(
        trade_id=trade.id,
        service_id=service.id,
        reference=generate_reference(trade.slug),
        start_at=start_at_utc,
        end_at=end_at_utc,
        customer_name=customer_name.strip(),
        customer_phone=customer_phone.strip(),
        customer_email=customer_email.strip().lower(),
        customer_postcode=customer_postcode.strip().upper().replace("  ", " "),
        customer_address=customer_address.strip() if customer_address else None,
        job_notes=job_notes.strip() if job_notes else None,
        photo_urls="\n".join(photo_urls) if photo_urls else None,
        deposit_pence=service.deposit_pence,
        status=BookingStatus.PENDING_PAYMENT,
        payment_status=PaymentStatus.PENDING,
    )
    db.session.add(booking)
    # Ensure booking.id exists for the event FK.
    try:
        db.session.flush()
    except IntegrityError as e:
        db.session.rollback()
        # Unique violation on (trade_id, start_at) — race we lost.
        raise SlotUnavailable("That slot has just been taken — please pick another.") from e

    _log_event(
        booking,
        "booking_created",
        actor="customer",
        description=f"Booking created by {customer_email}",
        to_status=BookingStatus.PENDING_PAYMENT.value,
        ip=ip,
    )
    db.session.commit()
    return booking


def find_by_reference(reference: str) -> Booking:
    booking = db.session.execute(
        select(Booking).where(Booking.reference == reference.upper())
    ).scalar_one_or_none()
    if not booking:
        raise BookingNotFound("No booking found with that reference.")
    return booking


def find_for_customer_lookup(
    *,
    reference: str | None,
    contact: str,
) -> Booking | None:
    """Customer 'find my booking' flow.

    If reference is provided, we require the contact (phone or email) to match.
    If reference is omitted, we match by contact — but we do NOT return results
    here; instead we email a magic link (that happens in the view).
    """
    contact = contact.strip().lower()
    if not reference:
        return None

    stmt = select(Booking).where(Booking.reference == reference.strip().upper())
    booking = db.session.execute(stmt).scalar_one_or_none()
    if not booking:
        return None

    # Accept email match OR last-7 digits of phone. Require 7 digits to
    # avoid tiny-input collisions.
    phone_norm = "".join(c for c in (booking.customer_phone or "") if c.isdigit())
    contact_norm = "".join(c for c in contact if c.isdigit())
    if booking.customer_email == contact:
        return booking
    if len(contact_norm) >= 7 and phone_norm.endswith(contact_norm[-7:]):
        return booking
    return None


def find_bookings_by_contact(contact: str) -> list[Booking]:
    """Used when sending magic links — find all bookings for an email/phone.

    Phone matching is done in Python because stored phones may contain
    spaces/punctuation that a raw SQL LIKE won't see through. We fetch a
    broad candidate set using email match + a last-N-digits LIKE, then
    tighten in Python.
    """
    contact = contact.strip().lower()
    contact_digits = "".join(c for c in contact if c.isdigit())

    conditions = [Booking.customer_email == contact]
    if contact_digits and len(contact_digits) >= 7:
        # Last 7 digits catches most UK phones even if stored with a leading
        # 0 vs +44. Overfetch is fine — we filter properly below.
        conditions.append(Booking.customer_phone.contains(contact_digits[-7:]))

    stmt = select(Booking).where(or_(*conditions))
    candidates = list(db.session.execute(stmt).scalars())

    if not contact_digits:
        return candidates  # email-only match

    # Python-side: normalise stored phone to digits and require last-7 match.
    suffix = contact_digits[-7:]
    out: list[Booking] = []
    seen: set[int] = set()
    for b in candidates:
        if b.id in seen:
            continue
        if b.customer_email == contact:
            out.append(b)
            seen.add(b.id)
            continue
        stored_digits = "".join(c for c in (b.customer_phone or "") if c.isdigit())
        if stored_digits.endswith(suffix):
            out.append(b)
            seen.add(b.id)
    return out


def transition(
    booking: Booking,
    to_status: BookingStatus,
    *,
    actor: str,
    description: str | None = None,
    ip: str | None = None,
) -> Booking:
    if to_status not in _ALLOWED.get(booking.status, set()):
        raise InvalidTransition(
            f"Cannot move booking from {booking.status.value} to {to_status.value}."
        )

    from_status = booking.status.value
    booking.status = to_status

    _log_event(
        booking,
        "status_changed",
        actor=actor,
        description=description,
        from_status=from_status,
        to_status=to_status.value,
        ip=ip,
    )
    db.session.commit()
    return booking


def mark_paid(
    booking: Booking,
    *,
    stripe_payment_intent_id: str,
    stripe_charge_id: str | None = None,
) -> Booking:
    """Called from the Stripe webhook handler after a successful payment.

    Race-safe: if the booking was cancelled between checkout-open and
    webhook-arrival, we record the payment but do NOT promote back to
    BOOKED. The caller (webhook handler) is responsible for triggering a
    refund in that case — since we've got the customer's money but the
    slot is gone.
    """
    if booking.payment_status == PaymentStatus.PAID:
        return booking  # idempotent

    booking.payment_status = PaymentStatus.PAID
    booking.stripe_payment_intent_id = stripe_payment_intent_id
    if stripe_charge_id:
        booking.stripe_charge_id = stripe_charge_id

    if booking.status == BookingStatus.PENDING_PAYMENT:
        booking.status = BookingStatus.BOOKED
        _log_event(
            booking,
            "status_changed",
            actor="stripe",
            description="Deposit captured; booking confirmed.",
            from_status=BookingStatus.PENDING_PAYMENT.value,
            to_status=BookingStatus.BOOKED.value,
        )
    elif booking.status == BookingStatus.CANCELLED:
        # Paid but cancelled (rare race) — payment recorded, caller should refund.
        _log_event(
            booking,
            "payment_on_cancelled",
            actor="stripe",
            description=(
                f"Deposit {booking.deposit_display} captured on a cancelled "
                "booking. Automatic refund required."
            ),
        )

    _log_event(
        booking,
        "payment_captured",
        actor="stripe",
        description=f"Deposit of {booking.deposit_display} captured.",
    )
    db.session.commit()
    return booking


def expire_stale_pending(older_than_minutes: int = 20) -> int:
    """Sweep job: cancel bookings stuck in PENDING_PAYMENT.

    Run periodically (e.g. cron every 5 minutes). Returns count cancelled.
    """
    cutoff = datetime.now(UTC) - timedelta(minutes=older_than_minutes)
    stale = list(
        db.session.execute(
            select(Booking).where(
                Booking.status == BookingStatus.PENDING_PAYMENT,
                Booking.created_at < cutoff,
            )
        ).scalars()
    )
    for b in stale:
        b.status = BookingStatus.CANCELLED
        _log_event(
            b,
            "status_changed",
            actor="system",
            description="Auto-cancelled: payment not completed within 20 minutes.",
            from_status=BookingStatus.PENDING_PAYMENT.value,
            to_status=BookingStatus.CANCELLED.value,
        )
    if stale:
        db.session.commit()
    return len(stale)


def compute_refund_pence(booking: Booking, *, now_utc: datetime | None = None) -> int:
    """Apply the trade's cancellation policy and return refundable pence.

    Policies (MVP):
      24h_full       — full refund if cancelling 24h+ before start.
      24h_partial    — full 24h+, 50% under 24h, none under 2h.
      non_refundable — no refunds.
    """
    now_utc = now_utc or datetime.now(UTC)
    # SQLite strips tz info on read; treat naive datetimes as UTC.
    start_at = booking.start_at if booking.start_at.tzinfo else booking.start_at.replace(tzinfo=UTC)
    hours_to_start = (start_at - now_utc).total_seconds() / 3600

    policy = booking.trade.cancellation_policy or "24h_full"
    deposit = booking.deposit_pence

    if policy == "non_refundable":
        return 0
    if policy == "24h_full":
        return deposit if hours_to_start >= 24 else 0
    if policy == "24h_partial":
        if hours_to_start >= 24:
            return deposit
        if hours_to_start >= 2:
            return deposit // 2
        return 0
    return 0


def cancel(
    booking: Booking,
    *,
    actor: str,
    reason: str | None = None,
    ip: str | None = None,
) -> tuple[Booking, int]:
    """Cancel a booking. Returns (booking, refund_pence).

    The refund is computed here but not issued — the caller triggers Stripe
    so side effects stay explicit.
    """
    if booking.is_terminal:
        raise PolicyViolation("This booking is already closed.")

    refund_pence = compute_refund_pence(booking) if booking.is_paid else 0

    transition(
        booking,
        BookingStatus.CANCELLED,
        actor=actor,
        description=reason or f"Cancelled by {actor}",
        ip=ip,
    )
    return booking, refund_pence
