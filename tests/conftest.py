"""Pytest fixtures.

SQLite in-memory for speed. Note: the partial unique index uses PG syntax,
so we skip its creation in tests (collisions are still caught by the service
layer's pre-check; the index is the belt-and-braces in prod).
"""
from __future__ import annotations

import pytest

from app import create_app
from app.extensions import db
from app.models import AvailabilityRule, Service, Trade
from config import TestingConfig


@pytest.fixture
def app():
    flask_app = create_app(TestingConfig)
    with flask_app.app_context():
        # SQLite can't do the partial index. Create everything else and
        # let the service layer enforce uniqueness in tests.
        from sqlalchemy import event
        from app.models import Booking  # noqa

        db.create_all()
        yield flask_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def trade(app):
    t = Trade(
        email="mark@example.com",
        slug="mckinney-plumbing",
        business_name="McKinney Plumbing & Heating",
        contact_name="Mark McKinney",
        trade_type="plumber",
        phone="02890241234",
        is_published=True,
        stripe_account_id="acct_test_123",
        stripe_charges_enabled=True,
    )
    t.set_password("testpassword")
    db.session.add(t)
    db.session.flush()

    db.session.add(
        Service(
            trade_id=t.id,
            name="Emergency leak repair",
            duration_minutes=60,
            deposit_pence=6000,
            icon="🚿",
            display_order=0,
        )
    )
    # Mon-Fri 9-17
    for wd in range(5):
        db.session.add(
            AvailabilityRule(
                trade_id=t.id, weekday=wd, start_time="09:00", end_time="17:00"
            )
        )
    db.session.commit()
    return t


@pytest.fixture
def service(trade):
    return trade.services[0]
