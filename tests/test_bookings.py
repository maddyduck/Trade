"""Tests for app.services.bookings."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from freezegun import freeze_time

from app.models import BookingStatus, PaymentStatus
from app.services import bookings as booking_svc
from app.services.errors import (
    BookingNotFound,
    InvalidTransition,
    SlotUnavailable,
)


def _make(trade, service, start=None):
    start = start or datetime(2025, 6, 17, 10, 0, tzinfo=UTC)
    return booking_svc.create_pending_booking(
        trade=trade,
        service=service,
        start_at_utc=start,
        customer_name="Sarah Hughes",
        customer_phone="07712445892",
        customer_email="sarah@example.com",
        customer_postcode="BT9 6AB",
        customer_address="42 Malone Road",
        job_notes="Leak under kitchen sink.",
    )


def test_create_pending_booking_happy_path(app, trade, service):
    b = _make(trade, service)
    assert b.status == BookingStatus.PENDING_PAYMENT
    assert b.payment_status == PaymentStatus.PENDING
    assert b.reference.startswith("MCK-")  # derived from slug
    assert len(b.events) == 1
    assert b.events[0].event_type == "booking_created"


def test_cannot_double_book(app, trade, service):
    start = datetime(2025, 6, 17, 10, 0, tzinfo=UTC)
    _make(trade, service, start=start)
    with pytest.raises(SlotUnavailable):
        _make(trade, service, start=start)


def test_transition_happy_path(app, trade, service):
    b = _make(trade, service)
    booking_svc.mark_paid(b, stripe_payment_intent_id="pi_test_123")
    assert b.status == BookingStatus.BOOKED

    booking_svc.transition(b, BookingStatus.ON_THE_WAY, actor="trade")
    assert b.status == BookingStatus.ON_THE_WAY

    booking_svc.transition(b, BookingStatus.ARRIVED, actor="trade")
    booking_svc.transition(b, BookingStatus.IN_PROGRESS, actor="trade")
    booking_svc.transition(b, BookingStatus.COMPLETE, actor="trade")
    assert b.status == BookingStatus.COMPLETE


def test_invalid_transition_rejected(app, trade, service):
    b = _make(trade, service)
    booking_svc.mark_paid(b, stripe_payment_intent_id="pi_test_123")
    with pytest.raises(InvalidTransition):
        booking_svc.transition(b, BookingStatus.COMPLETE, actor="trade")


def test_mark_paid_is_idempotent(app, trade, service):
    b = _make(trade, service)
    booking_svc.mark_paid(b, stripe_payment_intent_id="pi_1")
    booking_svc.mark_paid(b, stripe_payment_intent_id="pi_1")  # second call
    # Still just one payment_captured event plus the status_changed
    types = [e.event_type for e in b.events]
    assert types.count("payment_captured") == 1


def test_find_by_reference(app, trade, service):
    b = _make(trade, service)
    found = booking_svc.find_by_reference(b.reference)
    assert found.id == b.id
    with pytest.raises(BookingNotFound):
        booking_svc.find_by_reference("NOT-REAL-00")


def test_find_for_customer_lookup_email_match(app, trade, service):
    b = _make(trade, service)
    found = booking_svc.find_for_customer_lookup(
        reference=b.reference, contact="sarah@example.com"
    )
    assert found.id == b.id


def test_find_for_customer_lookup_phone_match(app, trade, service):
    b = _make(trade, service)
    found = booking_svc.find_for_customer_lookup(
        reference=b.reference, contact="07712445892"
    )
    assert found.id == b.id


def test_find_for_customer_lookup_wrong_contact_returns_none(app, trade, service):
    b = _make(trade, service)
    assert (
        booking_svc.find_for_customer_lookup(
            reference=b.reference, contact="someone-else@example.com"
        )
        is None
    )


def test_cancel(app, trade, service):
    b = _make(trade, service)
    booking_svc.mark_paid(b, stripe_payment_intent_id="pi_test")
    b, refund = booking_svc.cancel(b, actor="customer")
    assert b.status == BookingStatus.CANCELLED


@freeze_time("2025-06-15 10:00:00")  # 48h before the booking at 2025-06-17 10:00 UTC
def test_refund_policy_24h_full_full_refund(app, trade, service):
    trade.cancellation_policy = "24h_full"
    b = _make(trade, service)
    booking_svc.mark_paid(b, stripe_payment_intent_id="pi_1")
    refund = booking_svc.compute_refund_pence(b)
    assert refund == b.deposit_pence


@freeze_time("2025-06-17 09:00:00")  # 1h before
def test_refund_policy_24h_full_no_refund_close_to_slot(app, trade, service):
    trade.cancellation_policy = "24h_full"
    b = _make(trade, service)
    booking_svc.mark_paid(b, stripe_payment_intent_id="pi_1")
    refund = booking_svc.compute_refund_pence(b)
    assert refund == 0


@freeze_time("2025-06-17 06:00:00")  # 4h before
def test_refund_policy_24h_partial_half(app, trade, service):
    trade.cancellation_policy = "24h_partial"
    b = _make(trade, service)
    booking_svc.mark_paid(b, stripe_payment_intent_id="pi_1")
    refund = booking_svc.compute_refund_pence(b)
    assert refund == b.deposit_pence // 2


def test_expire_stale_pending(app, trade, service):
    b = _make(trade, service)
    # Backdate the booking's created_at to 30 mins ago
    b.created_at = datetime.now(UTC) - timedelta(minutes=30)
    from app.extensions import db as _db

    _db.session.commit()
    n = booking_svc.expire_stale_pending(older_than_minutes=20)
    assert n == 1
    assert b.status == BookingStatus.CANCELLED
