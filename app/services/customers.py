"""Detect repeat customers across bookings.

A customer is "returning" for a given trade if they have any prior
booking with that trade in any status except CANCELLED — and they're
identified by either email match or normalised-phone match.

This is read-only and called from templates and dashboards. We don't
materialise a Customer table in V1; this is a query helper.
"""
from __future__ import annotations

from sqlalchemy import and_, func, or_, select

from app.extensions import db
from app.models import Booking, BookingStatus


def is_returning_customer(booking: Booking) -> bool:
    """True if this customer has any prior booking with this trade."""
    if not booking or not booking.trade_id:
        return False
    return prior_booking_count(
        trade_id=booking.trade_id,
        email=booking.customer_email,
        phone=booking.customer_phone,
        before_booking_id=booking.id,
    ) > 0


def prior_booking_count(
    trade_id: int,
    email: str | None,
    phone: str | None,
    before_booking_id: int | None = None,
) -> int:
    """Count of prior non-cancelled bookings by this email/phone for this trade."""
    if not email and not phone:
        return 0

    conds = []
    if email:
        conds.append(Booking.customer_email == email.lower().strip())
    # We can do a SQL phone fragment match; perfect normalisation happens
    # only via Python, but we don't want to load all rows here.
    if phone:
        digits = "".join(c for c in phone if c.isdigit())
        if len(digits) >= 7:
            conds.append(Booking.customer_phone.contains(digits[-7:]))

    if not conds:
        return 0

    where = and_(
        Booking.trade_id == trade_id,
        Booking.status != BookingStatus.CANCELLED.value,
        or_(*conds),
    )
    if before_booking_id:
        where = and_(where, Booking.id != before_booking_id)

    return int(
        db.session.execute(select(func.count(Booking.id)).where(where)).scalar() or 0
    )


def last_booking_for_customer(
    trade_id: int,
    email: str | None,
    phone: str | None,
    before_booking_id: int | None = None,
) -> Booking | None:
    """Return the most recent prior booking, for the 'last seen on...' line."""
    if not email and not phone:
        return None

    conds = []
    if email:
        conds.append(Booking.customer_email == email.lower().strip())
    if phone:
        digits = "".join(c for c in phone if c.isdigit())
        if len(digits) >= 7:
            conds.append(Booking.customer_phone.contains(digits[-7:]))

    if not conds:
        return None

    where = and_(
        Booking.trade_id == trade_id,
        Booking.status != BookingStatus.CANCELLED.value,
        or_(*conds),
    )
    if before_booking_id:
        where = and_(where, Booking.id != before_booking_id)

    return db.session.execute(
        select(Booking).where(where).order_by(Booking.start_at.desc()).limit(1)
    ).scalar_one_or_none()
