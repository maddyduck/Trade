"""Honest response-time labels for the trade public page.

We compute "Mark usually replies within 2 hours" from real data:
  - Look at the gap between booking creation and the first BookingEvent
    that represents trade engagement (status moving past 'booked').
  - Take the median across the trade's last ~30 active bookings.
  - Map to a human label.

Falls back to a sensible default if there's not enough data — no fake
claims about response times for new trades.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.extensions import db
from app.models import Booking, BookingEvent, BookingStatus, Trade

# Minimum data points before we'll quote a number publicly
MIN_SAMPLES = 5

# How far back we look (days)
LOOKBACK_DAYS = 60


def median_response_minutes(trade: Trade) -> float | None:
    """Median minutes from booking creation to first trade engagement.

    Returns None if we don't have enough data.
    """
    cutoff = datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)

    # Bookings that progressed past 'booked' — i.e. trade did something.
    bookings = list(
        db.session.execute(
            select(Booking)
            .where(
                Booking.trade_id == trade.id,
                Booking.created_at >= cutoff,
                Booking.status.in_(
                    [
                        BookingStatus.ON_THE_WAY.value,
                        BookingStatus.ARRIVED.value,
                        BookingStatus.IN_PROGRESS.value,
                        BookingStatus.COMPLETE.value,
                    ]
                ),
            )
        ).scalars()
    )

    samples: list[float] = []
    for b in bookings:
        # Find first event past 'booked'
        first_action = next(
            (
                e for e in sorted(b.events, key=lambda e: e.created_at)
                if e.to_status and e.to_status.value not in (
                    "pending_payment", "booked"
                )
            ),
            None,
        )
        if not first_action:
            continue
        delta = (first_action.created_at - b.created_at).total_seconds() / 60.0
        if 0 < delta < 60 * 24 * 3:  # cap at 3 days to avoid outliers
            samples.append(delta)

    if len(samples) < MIN_SAMPLES:
        return None

    samples.sort()
    n = len(samples)
    if n % 2 == 1:
        return samples[n // 2]
    return (samples[n // 2 - 1] + samples[n // 2]) / 2.0


def response_time_label_for(trade: Trade) -> str | None:
    """Human-readable label, or None to omit the badge entirely."""
    median = median_response_minutes(trade)
    if median is None:
        return None
    if median < 30:
        return "within 30 minutes"
    if median < 60:
        return "within an hour"
    if median < 120:
        return "within 2 hours"
    if median < 240:
        return "within 4 hours"
    if median < 60 * 24:
        return "the same day"
    return "within a day"
