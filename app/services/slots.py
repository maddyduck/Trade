"""Slot generation.

Given a trade and a date range, produce a list of bookable slots.

Algorithm:
  1. For each date in the range:
     a. Skip if there's an AvailabilityBlock for that date.
     b. For each AvailabilityRule matching that weekday, produce slots of
        length service.duration + trade.buffer_minutes, stepping by
        trade.slot_minutes, within [rule.start, rule.end - duration].
     c. Remove any slot that collides with an existing active booking.
     d. Remove any slot that starts in the past (with a small grace window).
  2. Return a list of (local_start, utc_start, utc_end) tuples grouped by day.

Timezone handling:
  - AvailabilityRule times are LOCAL (Europe/London).
  - Database stores UTC.
  - We convert when generating. DST transitions just work because we build
    the local datetime first and let zoneinfo resolve the offset.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.extensions import db
from app.models import (
    ACTIVE_BOOKING_STATUSES,
    AvailabilityBlock,
    AvailabilityRule,
    Booking,
    Service,
    Trade,
)


@dataclass(frozen=True)
class Slot:
    local_start: datetime  # tz-aware, trade's local zone
    start_utc: datetime
    end_utc: datetime

    @property
    def display(self) -> str:
        return self.local_start.strftime("%H:%M")


@dataclass(frozen=True)
class DaySlots:
    local_date: date
    slots: list[Slot]

    @property
    def is_empty(self) -> bool:
        return len(self.slots) == 0


def _parse_hm(s: str) -> time:
    hh, mm = s.split(":")
    return time(int(hh), int(mm))


def _slot_duration(trade: Trade, service: Service) -> timedelta:
    return timedelta(minutes=service.duration_minutes + trade.buffer_minutes)


def _as_utc(dt: datetime) -> datetime:
    # SQLite strips tz info on read; treat naive datetimes as UTC.
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _collides(start_utc: datetime, end_utc: datetime, bookings: list[Booking]) -> bool:
    """Half-open interval overlap: [a_start, a_end) vs [b_start, b_end)."""
    for b in bookings:
        if start_utc < _as_utc(b.end_at) and _as_utc(b.start_at) < end_utc:
            return True
    return False


def generate_slots(
    trade: Trade,
    service: Service,
    *,
    from_date: date,
    days: int,
    tz_name: str = "Europe/London",
    now_utc: datetime | None = None,
) -> list[DaySlots]:
    """Generate bookable slots for a trade/service over `days` days."""
    tz = ZoneInfo(tz_name)
    now_utc = now_utc or datetime.now(UTC)
    grace = timedelta(minutes=30)  # don't offer slots starting in the next 30 mins

    to_date = from_date + timedelta(days=days - 1)

    # Load rules, blocks, and existing bookings in one go.
    rules = list(
        db.session.execute(
            select(AvailabilityRule).where(AvailabilityRule.trade_id == trade.id)
        ).scalars()
    )
    rules_by_weekday: dict[int, list[AvailabilityRule]] = {}
    for r in rules:
        rules_by_weekday.setdefault(r.weekday, []).append(r)

    blocks: set[date] = {
        b.block_date
        for b in db.session.execute(
            select(AvailabilityBlock).where(
                AvailabilityBlock.trade_id == trade.id,
                AvailabilityBlock.block_date >= from_date,
                AvailabilityBlock.block_date <= to_date,
            )
        ).scalars()
    }

    # Active bookings that could collide. Use UTC bounds matching the local range.
    range_start_utc = datetime.combine(from_date, time(0, 0), tzinfo=tz).astimezone(UTC)
    range_end_utc = datetime.combine(
        to_date + timedelta(days=1), time(0, 0), tzinfo=tz
    ).astimezone(UTC)

    bookings = list(
        db.session.execute(
            select(Booking).where(
                Booking.trade_id == trade.id,
                Booking.status.in_(ACTIVE_BOOKING_STATUSES),
                Booking.end_at > range_start_utc,
                Booking.start_at < range_end_utc,
            )
        ).scalars()
    )

    duration = _slot_duration(trade, service)
    step = timedelta(minutes=trade.slot_minutes)

    day_slots: list[DaySlots] = []

    for i in range(days):
        d = from_date + timedelta(days=i)

        if d in blocks:
            day_slots.append(DaySlots(local_date=d, slots=[]))
            continue

        weekday = d.weekday()
        slots_for_day: list[Slot] = []

        for rule in rules_by_weekday.get(weekday, []):
            rule_start_local = datetime.combine(d, _parse_hm(rule.start_time), tzinfo=tz)
            rule_end_local = datetime.combine(d, _parse_hm(rule.end_time), tzinfo=tz)

            cursor = rule_start_local
            while cursor + duration <= rule_end_local:
                start_utc = cursor.astimezone(UTC)
                end_utc = (cursor + duration).astimezone(UTC)

                if start_utc < now_utc + grace:
                    cursor += step
                    continue
                if _collides(start_utc, end_utc, bookings):
                    cursor += step
                    continue

                slots_for_day.append(
                    Slot(local_start=cursor, start_utc=start_utc, end_utc=end_utc)
                )
                cursor += step

        slots_for_day.sort(key=lambda s: s.local_start)
        day_slots.append(DaySlots(local_date=d, slots=slots_for_day))

    return day_slots
