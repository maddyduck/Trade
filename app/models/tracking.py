"""Live tracking — Uber-like 'on the way' marker.

When a trade taps "On the way" they (optionally) start a tracking session
and share location while travelling. Customer sees a moving marker on a
map. Session expires when trade marks "Arrived" or after a configurable
TTL — whichever first.

We do NOT store continuous high-resolution location history beyond the
session lifetime — only the most recent ping and a small buffer. After
the session ends, location data is purged by the sweep cron.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models._base import IdMixin, TimestampMixin


class TrackingStatus(str, enum.Enum):
    ACTIVE = "active"
    ARRIVED = "arrived"
    EXPIRED = "expired"
    STOPPED = "stopped"  # trade ended manually


class TrackingSession(IdMixin, TimestampMixin, db.Model):
    """Per-booking tracking session. One per 'on the way' lifecycle."""
    __tablename__ = "tracking_sessions"

    booking_id: Mapped[int] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Public token used in customer's tracking URL — unguessable.
    public_token: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )

    status: Mapped[TrackingStatus] = mapped_column(
        db.Enum(
            TrackingStatus,
            name="tracking_status",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        default=TrackingStatus.ACTIVE,
        nullable=False,
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ETA the trade said when starting (in minutes from start)
    eta_minutes: Mapped[int | None] = mapped_column(Integer)

    booking = relationship("Booking", backref="tracking_sessions")
    pings = relationship(
        "TrackingPing",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="TrackingPing.created_at.desc()",
    )


class TrackingPing(IdMixin, TimestampMixin, db.Model):
    """A single location update from the trade's browser."""
    __tablename__ = "tracking_pings"

    session_id: Mapped[int] = mapped_column(
        ForeignKey("tracking_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    accuracy_m: Mapped[float | None] = mapped_column(Float)
    heading_deg: Mapped[float | None] = mapped_column(Float)

    session = relationship("TrackingSession", back_populates="pings")

    __table_args__ = (
        Index("ix_tracking_pings_session_created", "session_id", "created_at"),
    )
