"""Flask CLI commands: seeding, ops tasks.

Usage:
  flask seed-demo       # create a demo trade with sample services
  flask sweep-pending   # expire stale PENDING_PAYMENT bookings
"""
from __future__ import annotations

import click
from flask import Flask

from app.extensions import db


def register_cli(app: Flask) -> None:
    @app.cli.command("seed-demo")
    def seed_demo():
        """Create a demo trade with services and availability."""
        from app.models import AvailabilityRule, Service, Trade

        if db.session.query(Trade).filter_by(email="mark@example.com").first():
            click.echo("Demo trade already exists.")
            return

        trade = Trade(
            email="mark@example.com",
            slug="mckinney-plumbing",
            business_name="McKinney Plumbing & Heating",
            contact_name="Mark McKinney",
            trade_type="plumber",
            bio="12 years sorting boilers, leaks and bathrooms across Belfast. "
            "Honest pricing, on-time arrivals, no surprises.",
            phone="02890241234",
            service_area="Belfast, Lisburn, Holywood, Carrickfergus",
            credentials="Gas Safe Register #586342 | CIPHE member | £2m Public Liability",
            years_active=12,
            is_published=True,
            stripe_charges_enabled=True,  # dev convenience
            stripe_payouts_enabled=True,
        )
        trade.set_password("sortedpassword")
        db.session.add(trade)
        db.session.flush()

        services = [
            ("Emergency leak repair", "Burst pipes, leaks, urgent water issues.", 90, 6000, "🚿", 0),
            ("Boiler service & callout", "Annual service, no heat, error codes.", 60, 4500, "🔥", 1),
            ("General plumbing visit", "Blocked drains, dripping taps, fixture swaps.", 60, 3000, "🔧", 2),
            ("Bathroom consultation", "On-site quote for a new bathroom.", 45, 3000, "🛁", 3),
        ]
        for name, desc, dur, dep, icon, order in services:
            db.session.add(
                Service(
                    trade_id=trade.id,
                    name=name,
                    description=desc,
                    duration_minutes=dur,
                    deposit_pence=dep,
                    icon=icon,
                    display_order=order,
                )
            )

        # Mon-Fri 08:00-17:00, Sat 09:00-12:00
        for wd in range(5):
            db.session.add(
                AvailabilityRule(
                    trade_id=trade.id, weekday=wd, start_time="08:00", end_time="17:00"
                )
            )
        db.session.add(
            AvailabilityRule(
                trade_id=trade.id, weekday=5, start_time="09:00", end_time="12:00"
            )
        )

        db.session.commit()
        click.echo("Seeded demo trade: mark@example.com / sortedpassword")
        click.echo("Public page: /mckinney-plumbing")

    @app.cli.command("sweep-pending")
    def sweep_pending():
        from app.services.bookings import expire_stale_pending

        n = expire_stale_pending()
        click.echo(f"Expired {n} stale pending bookings.")

    @app.cli.command("reconcile")
    @click.argument("reference")
    def reconcile(reference):
        """Pull Stripe state for one booking and apply to the local DB."""
        from app.models import Booking
        from app.services import payments

        booking = db.session.query(Booking).filter(Booking.reference == reference.upper()).one_or_none()
        if not booking:
            click.echo(f"No booking found with reference {reference}")
            return
        result = payments.reconcile_booking(booking)
        for line in _format_reconcile(result):
            click.echo(line)

    @app.cli.command("reconcile-recent")
    @click.option("--hours", default=6, help="How far back to look")
    def reconcile_recent(hours):
        """Reconcile all bookings created in the last N hours.

        Useful after a webhook outage: 'flask reconcile-recent --hours 2'.
        """
        from datetime import UTC, datetime, timedelta

        from app.models import Booking, BookingStatus
        from app.services import payments

        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        candidates = (
            db.session.query(Booking)
            .filter(Booking.created_at >= cutoff)
            .filter(Booking.status.in_([BookingStatus.PENDING_PAYMENT, BookingStatus.BOOKED]))
            .all()
        )
        click.echo(f"Reconciling {len(candidates)} booking(s) from last {hours}h")
        for b in candidates:
            result = payments.reconcile_booking(b)
            actions = ", ".join(result.get("actions", [])) or "no_change"
            click.echo(f"  {b.reference}: {actions}")

    @app.cli.command("stripe-events")
    @click.option("--limit", default=20)
    def stripe_events(limit):
        """List recent Stripe events we've processed."""
        from app.models import StripeEvent

        rows = (
            db.session.query(StripeEvent)
            .order_by(StripeEvent.received_at.desc())
            .limit(limit)
            .all()
        )
        for r in rows:
            when = r.received_at.strftime("%Y-%m-%d %H:%M:%S")
            click.echo(
                f"{when}  {r.event_id}  {r.event_type:<35}  "
                f"{r.processing_result:<8}  {r.note or ''}"
            )

    @app.cli.command("stripe-events-cleanup")
    @click.option("--days", default=60, help="Keep events from last N days")
    def stripe_events_cleanup(days):
        """Delete handled/skipped Stripe events older than N days.

        Keeps 'errored' rows forever so they don't silently disappear.
        """
        from datetime import UTC, datetime, timedelta

        from app.models import StripeEvent

        cutoff = datetime.now(UTC) - timedelta(days=days)
        n = (
            db.session.query(StripeEvent)
            .filter(
                StripeEvent.received_at < cutoff,
                StripeEvent.processing_result.in_(["handled", "skipped"]),
            )
            .delete(synchronize_session=False)
        )
        db.session.commit()
        click.echo(f"Deleted {n} old Stripe event rows.")

    @app.cli.command("chase-invoices")
    def chase_invoices():
        """Send reminder emails for overdue invoices.

        Run hourly or daily via cron. Idempotent — invoices already
        chased within the last 7 days are skipped automatically.
        """
        from app.services import invoices as invoice_svc

        due = invoice_svc.find_invoices_due_for_chase()
        if not due:
            click.echo("No invoices need chasing today.")
            return

        click.echo(f"Chasing {len(due)} invoice(s):")
        for inv in due:
            try:
                invoice_svc.chase_invoice(inv)
                click.echo(
                    f"  ✓ {inv.number} ({inv.days_overdue}d overdue, "
                    f"chase #{inv.chase_count})"
                )
            except Exception as e:  # noqa: BLE001
                click.echo(f"  ✗ {inv.number}: {e}", err=True)

    # ---------- Admin / support commands ----------

    @app.cli.command("find-booking")
    @click.argument("query")
    def find_booking(query):
        """Find bookings by reference, email, or phone digits.

        Examples:
          flask find-booking MCK-4827-3F
          flask find-booking customer@example.com
          flask find-booking 07712445892
        """
        from app.services import bookings as booking_svc

        # Try reference first
        if "-" in query and len(query) <= 14:
            b = booking_svc.find_by_reference(query.upper())
            if b:
                _print_booking(b)
                return

        matches = booking_svc.find_bookings_by_contact(query)
        if not matches:
            click.echo("No bookings found.")
            return
        click.echo(f"Found {len(matches)} booking(s):")
        for b in matches:
            _print_booking(b)
            click.echo("")

    @app.cli.command("issue-magic-link")
    @click.argument("reference")
    def issue_magic_link(reference):
        """Generate a magic link for a booking — for support use.

        Use when a customer can't find their email and needs to manage
        their booking. Link expires in 24h same as a normal magic link.
        """
        from app.services import bookings as booking_svc
        from app.services.magic_links import make_booking_token
        from flask import url_for

        b = booking_svc.find_by_reference(reference.upper())
        if not b:
            click.echo("No booking with that reference.")
            return

        token = make_booking_token(b.id, b.customer_email)
        # Build URL — works in CLI context using SERVER_NAME or APP_BASE_URL
        path = url_for("public.manage_booking", token=token)
        base = app.config.get("APP_BASE_URL", "http://localhost:5000")
        click.echo(f"Magic link for {reference}:")
        click.echo(f"  {base}{path}")
        click.echo(f"  (expires in 24h, customer email: {b.customer_email})")

    @app.cli.command("trade-info")
    @click.argument("email")
    def trade_info(email):
        """Show a trade's setup status — for onboarding support."""
        from sqlalchemy import select

        from app.models import Trade

        t = db.session.execute(
            select(Trade).where(Trade.email == email.lower())
        ).scalar_one_or_none()
        if not t:
            click.echo(f"No trade with email {email}")
            return

        click.echo(f"Trade #{t.id} — {t.business_name}")
        click.echo(f"  contact: {t.contact_name} <{t.email}>")
        click.echo(f"  slug:    /{t.slug}")
        click.echo(f"  phone:   {t.phone}")
        click.echo(f"  area:    {t.service_area or '(not set)'}")
        click.echo(f"  active services: {sum(1 for s in t.services if s.is_active)}")
        click.echo(f"  availability rules: {len(t.availability_rules)}")
        click.echo(f"  stripe charges enabled: {t.stripe_charges_enabled}")
        click.echo(f"  stripe payouts enabled: {t.stripe_payouts_enabled}")
        click.echo(f"  published: {t.is_published}")
        click.echo(f"  can_accept_bookings: {t.can_accept_bookings}")


def _print_booking(b) -> None:
    click.echo(
        f"  {b.reference}  {b.start_at.strftime('%Y-%m-%d %H:%M')}  "
        f"{b.status.value:<14}  {b.customer_name}  {b.customer_email}"
    )


def _format_reconcile(result) -> list[str]:
    lines = [f"Reconciled {result['reference']}:"]
    lines.append(f"  before: {result['before']}")
    for a in result.get("actions", []):
        lines.append(f"  action: {a}")
    if "after" in result:
        lines.append(f"  after:  {result['after']}")
    return lines
