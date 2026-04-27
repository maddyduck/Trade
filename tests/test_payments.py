"""Tests for Stripe hardening.

These focus on the logic we can test without hitting Stripe:
  - webhook event deduplication via StripeEvent
  - mark_paid race-safety (cancelled → doesn't become BOOKED)
  - statement suffix sanitisation
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.extensions import db
from app.models import (
    Booking,
    BookingStatus,
    PaymentStatus,
    StripeEvent,
)
from app.services import bookings as booking_svc
from app.services.payments import _statement_suffix


def _make_booking(trade, service, *, status=BookingStatus.PENDING_PAYMENT):
    b = Booking(
        trade_id=trade.id,
        service_id=service.id,
        reference="MCK-0001-AB",
        start_at=datetime(2030, 6, 17, 10, 0, tzinfo=UTC),
        end_at=datetime(2030, 6, 17, 11, 0, tzinfo=UTC),
        customer_name="Sarah",
        customer_phone="07000000000",
        customer_email="s@example.com",
        customer_postcode="BT9 6AB",
        deposit_pence=6000,
        status=status,
        payment_status=PaymentStatus.PENDING,
    )
    db.session.add(b)
    db.session.commit()
    return b


# --- StripeEvent dedup -----------------------------------------------------


def test_stripe_event_unique_on_event_id(app):
    db.session.add(StripeEvent(event_id="evt_abc", event_type="x"))
    db.session.commit()
    db.session.add(StripeEvent(event_id="evt_abc", event_type="x"))
    with pytest.raises(Exception):
        db.session.commit()
    db.session.rollback()


# --- mark_paid race safety -------------------------------------------------


def test_mark_paid_does_not_reopen_cancelled(app, trade, service):
    b = _make_booking(trade, service, status=BookingStatus.CANCELLED)
    booking_svc.mark_paid(b, stripe_payment_intent_id="pi_xyz")
    # Payment captured locally, but booking stays cancelled.
    assert b.payment_status == PaymentStatus.PAID
    assert b.status == BookingStatus.CANCELLED
    types = [e.event_type for e in b.events]
    assert "payment_on_cancelled" in types


def test_mark_paid_promotes_pending_to_booked(app, trade, service):
    b = _make_booking(trade, service, status=BookingStatus.PENDING_PAYMENT)
    booking_svc.mark_paid(b, stripe_payment_intent_id="pi_xyz")
    assert b.payment_status == PaymentStatus.PAID
    assert b.status == BookingStatus.BOOKED


def test_mark_paid_idempotent(app, trade, service):
    b = _make_booking(trade, service, status=BookingStatus.PENDING_PAYMENT)
    booking_svc.mark_paid(b, stripe_payment_intent_id="pi_1")
    booking_svc.mark_paid(b, stripe_payment_intent_id="pi_1")
    # Exactly one payment_captured event
    types = [e.event_type for e in b.events]
    assert types.count("payment_captured") == 1


# --- statement suffix sanitisation ----------------------------------------


def test_statement_suffix_strips_ampersand():
    assert _statement_suffix("McKinney & Sons") == "MCKINNEY AND"


def test_statement_suffix_capped_at_12():
    assert len(_statement_suffix("Supercalifragilistic")) == 12


def test_statement_suffix_strips_punctuation():
    assert _statement_suffix("Joe's Plumbing, Ltd.") == "JOES PLUMBIN"


def test_statement_suffix_fallback_on_empty():
    assert _statement_suffix("!!!") == "SORTED"
