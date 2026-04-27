"""Tests for app.services.slots."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from freezegun import freeze_time

from app.extensions import db
from app.models import AvailabilityBlock, Booking, BookingStatus, PaymentStatus
from app.services.slots import generate_slots


@freeze_time("2025-06-16 06:00:00")  # Monday, 06:00 UTC = 07:00 BST
def test_generates_slots_for_workdays(app, trade, service):
    """Mon-Fri 9-17 → 8 hour-long slots per workday."""
    start = date(2025, 6, 16)  # Monday
    days = generate_slots(trade, service, from_date=start, days=5)
    assert len(days) == 5
    # 9, 10, 11, 12, 13, 14, 15, 16 = 8 starts within 09:00-17:00 for a 60-min service
    for d in days:
        assert len(d.slots) == 8


@freeze_time("2025-06-16 06:00:00")
def test_no_slots_on_weekend(app, trade, service):
    start = date(2025, 6, 21)  # Saturday
    days = generate_slots(trade, service, from_date=start, days=2)
    assert all(d.is_empty for d in days)


@freeze_time("2025-06-16 10:30:00")  # 11:30 BST
def test_grace_window_excludes_imminent_slots(app, trade, service):
    start = date(2025, 6, 16)
    days = generate_slots(trade, service, from_date=start, days=1)
    # Now is 11:30 BST. Grace is 30 min. So 12:00, 13:00, 14:00, 15:00, 16:00 are available. 11:00 is in the past.
    times = [s.display for s in days[0].slots]
    assert "09:00" not in times
    assert "10:00" not in times
    assert "11:00" not in times
    assert "12:00" in times


@freeze_time("2025-06-16 06:00:00")
def test_availability_block_empties_day(app, trade, service):
    start = date(2025, 6, 17)  # Tuesday
    db.session.add(AvailabilityBlock(trade_id=trade.id, block_date=start, reason="holiday"))
    db.session.commit()
    days = generate_slots(trade, service, from_date=start, days=1)
    assert days[0].is_empty


@freeze_time("2025-06-16 06:00:00")
def test_existing_booking_removes_slot(app, trade, service):
    start = date(2025, 6, 17)  # Tuesday
    # Book Tuesday 10:00 local (09:00 UTC since BST)
    booking_start = datetime(2025, 6, 17, 9, 0, tzinfo=UTC)
    booking_end = booking_start + timedelta(hours=1)
    db.session.add(
        Booking(
            trade_id=trade.id,
            service_id=service.id,
            reference="TST-0001-AB",
            start_at=booking_start,
            end_at=booking_end,
            customer_name="Test",
            customer_phone="07000000000",
            customer_email="t@example.com",
            customer_postcode="BT1 1AA",
            deposit_pence=6000,
            status=BookingStatus.BOOKED,
            payment_status=PaymentStatus.PAID,
        )
    )
    db.session.commit()

    days = generate_slots(trade, service, from_date=start, days=1)
    times = [s.display for s in days[0].slots]
    assert "10:00" not in times  # taken
    assert "09:00" in times  # still free
    assert "11:00" in times  # still free
