"""Live tracking — Uber-like 'on the way' marker.

Trade taps "On the way", optionally starts a tracking session, picks an
ETA, and shares their location for that single trip. Customer sees a
map with a moving marker that refreshes every 30 seconds.

Privacy & data minimisation:
  - Tracking is opt-in, per-trip
  - Session expires after `expires_at` regardless of activity
  - Location pings are deleted when session ends, plus a sweep cron
  - Trade can stop at any time
"""
from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from flask import current_app
from sqlalchemy import and_, select

from app.extensions import db
from app.models import (
    Booking,
    BookingStatus,
    TrackingPing,
    TrackingSession,
)
from app.models.tracking import TrackingStatus
from app.services.errors import DomainError, InvalidTransition


def start_tracking_session(booking: Booking, eta_minutes: int | None = None) -> TrackingSession:
    """Begin a new tracking session for a booking that's just gone 'on_the_way'.

    Closes any prior active session for the same booking.
    """
    if booking.status != BookingStatus.ON_THE_WAY:
        raise InvalidTransition(
            "Tracking can only start when booking is 'on the way'."
        )

    # Close any prior session
    prior = db.session.execute(
        select(TrackingSession).where(
            and_(
                TrackingSession.booking_id == booking.id,
                TrackingSession.status == TrackingStatus.ACTIVE.value,
            )
        )
    ).scalars()
    for s in prior:
        s.status = TrackingStatus.STOPPED
        s.ended_at = datetime.now(UTC)

    ttl = current_app.config.get("TRACKING_LINK_TTL_MINUTES", 60)
    now = datetime.now(UTC)

    session = TrackingSession(
        booking_id=booking.id,
        public_token=secrets.token_urlsafe(24),
        started_at=now,
        expires_at=now + timedelta(minutes=ttl),
        eta_minutes=eta_minutes,
    )
    db.session.add(session)
    db.session.commit()
    return session


def end_tracking_session(
    session: TrackingSession, status: TrackingStatus = TrackingStatus.ARRIVED
) -> None:
    """End and purge ping history."""
    session.status = status
    session.ended_at = datetime.now(UTC)
    db.session.query(TrackingPing).filter(
        TrackingPing.session_id == session.id
    ).delete(synchronize_session=False)
    db.session.commit()


def end_active_session_for_booking(booking: Booking) -> None:
    """Called when a booking transitions to ARRIVED — close any active session."""
    sessions = db.session.execute(
        select(TrackingSession).where(
            and_(
                TrackingSession.booking_id == booking.id,
                TrackingSession.status == TrackingStatus.ACTIVE.value,
            )
        )
    ).scalars()
    for s in sessions:
        end_tracking_session(s, TrackingStatus.ARRIVED)


def record_ping(
    session: TrackingSession,
    latitude: float,
    longitude: float,
    accuracy_m: float | None = None,
    heading_deg: float | None = None,
) -> TrackingPing:
    """Log a location update from the trade's browser."""
    if session.status != TrackingStatus.ACTIVE:
        raise DomainError("Tracking session is not active")
    if session.expires_at < datetime.now(UTC):
        # Auto-expire
        session.status = TrackingStatus.EXPIRED
        session.ended_at = datetime.now(UTC)
        db.session.commit()
        raise DomainError("Tracking session has expired")

    # Sanity-check coordinates
    if not (-90 <= latitude <= 90) or not (-180 <= longitude <= 180):
        raise DomainError("Invalid coordinates")

    ping = TrackingPing(
        session_id=session.id,
        latitude=latitude,
        longitude=longitude,
        accuracy_m=accuracy_m,
        heading_deg=heading_deg,
    )
    db.session.add(ping)

    # Trim to last ~20 pings to keep storage tiny
    keep_ids = db.session.execute(
        select(TrackingPing.id)
        .where(TrackingPing.session_id == session.id)
        .order_by(TrackingPing.created_at.desc())
        .limit(20)
    ).scalars().all()
    if keep_ids:
        db.session.query(TrackingPing).filter(
            and_(
                TrackingPing.session_id == session.id,
                TrackingPing.id.notin_(keep_ids),
            )
        ).delete(synchronize_session=False)

    db.session.commit()
    return ping


def latest_ping(session: TrackingSession) -> TrackingPing | None:
    return db.session.execute(
        select(TrackingPing)
        .where(TrackingPing.session_id == session.id)
        .order_by(TrackingPing.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def get_active_session(booking: Booking) -> TrackingSession | None:
    """Currently-running session for a booking, if any."""
    sess = db.session.execute(
        select(TrackingSession).where(
            and_(
                TrackingSession.booking_id == booking.id,
                TrackingSession.status == TrackingStatus.ACTIVE.value,
            )
        )
    ).scalar_one_or_none()
    if sess and sess.expires_at < datetime.now(UTC):
        sess.status = TrackingStatus.EXPIRED
        sess.ended_at = datetime.now(UTC)
        db.session.commit()
        return None
    return sess


def session_by_token(token: str) -> TrackingSession | None:
    return db.session.execute(
        select(TrackingSession).where(TrackingSession.public_token == token)
    ).scalar_one_or_none()


def sweep_expired_sessions() -> int:
    """Mark expired sessions as such and purge their pings.

    Run periodically (every 5 minutes) via cron.
    """
    now = datetime.now(UTC)
    expired = list(
        db.session.execute(
            select(TrackingSession).where(
                and_(
                    TrackingSession.status == TrackingStatus.ACTIVE.value,
                    TrackingSession.expires_at < now,
                )
            )
        ).scalars()
    )
    for s in expired:
        s.status = TrackingStatus.EXPIRED
        s.ended_at = now
        db.session.query(TrackingPing).filter(
            TrackingPing.session_id == s.id
        ).delete(synchronize_session=False)

    db.session.commit()
    return len(expired)
