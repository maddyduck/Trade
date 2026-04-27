"""Trade-facing dashboard routes."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import and_, func, select

from app.dashboard.forms import AvailabilityRuleForm, ProfileForm, ServiceForm
from app.extensions import db
from app.models import (
    ACTIVE_BOOKING_STATUSES,
    AvailabilityRule,
    Booking,
    BookingStatus,
    Service,
)
from app.services import bookings as booking_svc
from app.services.errors import DomainError

bp = Blueprint("dashboard", __name__, template_folder="../templates/dashboard")


@bp.before_request
@login_required
def _require_login():
    pass


def _tz():
    return ZoneInfo(current_app.config["APP_TIMEZONE"])


def _local_day_bounds(d):
    tz = _tz()
    start_local = datetime.combine(d, datetime.min.time(), tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


# ---------- Today ----------


@bp.route("/")
def today():
    tz = _tz()
    today_local = datetime.now(tz).date()
    start_utc, end_utc = _local_day_bounds(today_local)
    tomorrow_start, tomorrow_end = _local_day_bounds(today_local + timedelta(days=1))

    todays = _list_bookings(current_user.id, start_utc, end_utc)
    tomorrows = _list_bookings(current_user.id, tomorrow_start, tomorrow_end)

    # Next up: first non-complete booking today.
    next_up = next(
        (b for b in todays if b.status != BookingStatus.COMPLETE and not b.is_terminal),
        None,
    )
    deposits_today_pence = sum(
        b.deposit_pence for b in todays if b.status != BookingStatus.CANCELLED
    )
    done_today = sum(1 for b in todays if b.status == BookingStatus.COMPLETE)

    # This week deposits
    week_start_local = datetime.combine(
        today_local - timedelta(days=today_local.weekday()),
        datetime.min.time(),
        tzinfo=tz,
    )
    week_start_utc = week_start_local.astimezone(UTC)
    week_end_utc = (week_start_local + timedelta(days=7)).astimezone(UTC)
    deposits_week_pence = (
        db.session.execute(
            select(func.coalesce(func.sum(Booking.deposit_pence), 0)).where(
                Booking.trade_id == current_user.id,
                Booking.status.in_(ACTIVE_BOOKING_STATUSES),
                Booking.start_at >= week_start_utc,
                Booking.start_at < week_end_utc,
            )
        ).scalar()
        or 0
    )

    # --- Onboarding checklist for new trades ---
    has_services = any(s.is_active for s in current_user.services)
    has_availability = len(current_user.availability_rules) > 0
    has_stripe = bool(current_user.stripe_charges_enabled)
    is_published = bool(current_user.is_published)

    # Show checklist until everything is ticked AND they have at least one
    # booking (so it doesn't feel stale after day 1).
    has_any_booking = (
        db.session.query(Booking.id)
        .filter(Booking.trade_id == current_user.id)
        .first()
        is not None
    )
    show_onboarding = not (
        has_services and has_availability and has_stripe and is_published and has_any_booking
    )

    return render_template(
        "dashboard/today.html",
        todays=todays,
        tomorrows=tomorrows,
        next_up=next_up,
        done_today=done_today,
        deposits_today_pence=deposits_today_pence,
        deposits_week_pence=deposits_week_pence,
        today_local=today_local,
        show_onboarding=show_onboarding,
        onboarding={
            "services": has_services,
            "availability": has_availability,
            "stripe": has_stripe,
            "published": is_published,
            "first_booking": has_any_booking,
        },
    )


# ---------- Week ----------


@bp.route("/week")
def week():
    tz = _tz()
    today_local = datetime.now(tz).date()
    days = [today_local + timedelta(days=i) for i in range(7)]

    start_utc, _ = _local_day_bounds(days[0])
    _, end_utc = _local_day_bounds(days[-1])

    all_bookings = _list_bookings(current_user.id, start_utc, end_utc)
    by_day: dict = {d: [] for d in days}
    for b in all_bookings:
        local_date = b.start_at.astimezone(tz).date()
        if local_date in by_day:
            by_day[local_date].append(b)

    return render_template("dashboard/week.html", days=days, by_day=by_day)


# ---------- History ----------


@bp.route("/history")
def history():
    """Past bookings, most recent first. Grouped by week with totals."""
    tz = _tz()
    now_local = datetime.now(tz)
    today_local = now_local.date()

    # Look back 8 weeks by default.
    earliest_local = datetime.combine(
        today_local - timedelta(weeks=8),
        datetime.min.time(),
        tzinfo=tz,
    )
    earliest_utc = earliest_local.astimezone(UTC)
    now_utc = now_local.astimezone(UTC)

    rows = list(
        db.session.execute(
            select(Booking)
            .where(
                Booking.trade_id == current_user.id,
                Booking.start_at >= earliest_utc,
                Booking.start_at < now_utc,
                Booking.status != BookingStatus.PENDING_PAYMENT,
            )
            .order_by(Booking.start_at.desc())
        ).scalars()
    )

    # Group by ISO week.
    from collections import OrderedDict

    buckets: "OrderedDict[tuple[int, int], dict]" = OrderedDict()
    for b in rows:
        local = b.start_at.astimezone(tz)
        iso_year, iso_week, _ = local.isocalendar()
        key = (iso_year, iso_week)
        bucket = buckets.setdefault(
            key,
            {"label": _week_label(local, today_local), "rows": [], "total_pence": 0, "count": 0, "complete": 0},
        )
        bucket["rows"].append(b)
        if b.status == BookingStatus.COMPLETE:
            bucket["total_pence"] += b.deposit_pence
            bucket["complete"] += 1
        bucket["count"] += 1

    return render_template("dashboard/history.html", buckets=list(buckets.values()))


def _week_label(booking_local: datetime, today_local) -> str:
    d = booking_local.date()
    iso_y, iso_w, _ = d.isocalendar()
    today_y, today_w, _ = today_local.isocalendar()
    if (iso_y, iso_w) == (today_y, today_w):
        return "This week"
    last = today_local - timedelta(days=7)
    last_y, last_w, _ = last.isocalendar()
    if (iso_y, iso_w) == (last_y, last_w):
        return "Last week"
    # Else: "Week of 5 May"
    # Start of that ISO week (Mon):
    from datetime import date as date_cls

    monday = date_cls.fromisocalendar(iso_y, iso_w, 1)
    return f"Week of {monday.strftime('%-d %b')}"


# ---------- Booking detail ----------


@bp.route("/bookings/<int:booking_id>")
def booking_detail(booking_id: int):
    booking = _owned_booking_or_404(booking_id)
    return render_template("dashboard/booking_detail.html", booking=booking)


@bp.route("/bookings/<int:booking_id>/advance", methods=["POST"])
def advance_booking(booking_id: int):
    booking = _owned_booking_or_404(booking_id)
    next_status = _next_status(booking.status)
    if not next_status:
        flash("This booking is already complete.", "info")
        return redirect(url_for("dashboard.booking_detail", booking_id=booking.id))
    try:
        booking_svc.transition(
            booking, next_status, actor="trade", ip=request.remote_addr
        )
    except DomainError as e:
        flash(e.message, "error")
        return redirect(url_for("dashboard.booking_detail", booking_id=booking.id))

    # Live tracking lifecycle hooks:
    #   on_the_way  -> if "share location" was checked, auto-start a tracking session
    #   arrived     -> end any active session
    if next_status == BookingStatus.ON_THE_WAY:
        share = (request.form.get("share_location") or "").lower() in ("on", "true", "1")
        eta_minutes_text = (request.form.get("eta_minutes") or "").strip()
        eta_minutes = None
        if eta_minutes_text.isdigit():
            eta_minutes = max(0, min(int(eta_minutes_text), 240))
        if share or eta_minutes is not None:
            from app.services import tracking as tracking_svc

            try:
                session = tracking_svc.start_tracking_session(booking, eta_minutes=eta_minutes)
                # Build customer URL for SMS/email notification
                from flask import url_for as _u

                track_url = _u(
                    "public_tracking.show", token=session.public_token, _external=True
                )
                # Send SMS / email — best-effort, don't fail request if either fails.
                _notify_customer_on_the_way(booking, eta_minutes, track_url if share else None)
            except DomainError as e:
                flash(f"Tracking didn't start: {e}", "info")

    elif next_status == BookingStatus.ARRIVED:
        from app.services import tracking as tracking_svc

        tracking_svc.end_active_session_for_booking(booking)
        _notify_customer_arrived(booking)

    elif next_status == BookingStatus.COMPLETE:
        _notify_customer_complete(booking)

    return redirect(url_for("dashboard.booking_detail", booking_id=booking.id))


def _notify_customer_on_the_way(booking, eta_minutes, track_url):
    """Best-effort customer notification when trade goes 'on the way'."""
    from app.services import email as email_svc
    from app.services import sms as sms_svc

    eta_str = f"~{eta_minutes} min" if eta_minutes else "soon"
    try:
        email_svc.send_email(
            to=booking.customer_email,
            subject=f"{booking.trade.contact_name.split()[0]} is on the way",
            template="emails/on_the_way",
            booking=booking,
            trade=booking.trade,
            eta_str=eta_str,
            track_url=track_url,
        )
    except Exception:  # noqa: BLE001
        pass

    if booking.customer_phone:
        msg = f"{booking.trade.contact_name.split()[0]} from {booking.trade.business_name} is on the way ({eta_str})."
        if track_url:
            msg += f" Track here: {track_url}"
        sms_svc.send_sms(booking.customer_phone, msg)


def _notify_customer_arrived(booking):
    from app.services import sms as sms_svc

    if booking.customer_phone:
        sms_svc.send_sms(
            booking.customer_phone,
            f"{booking.trade.contact_name.split()[0]} from {booking.trade.business_name} has arrived.",
        )


def _notify_customer_complete(booking):
    from app.services import sms as sms_svc

    if booking.customer_phone:
        sms_svc.send_sms(
            booking.customer_phone,
            f"Job complete. Thanks for booking {booking.trade.business_name}!",
        )


@bp.route("/bookings/<int:booking_id>/no-show", methods=["POST"])
def mark_no_show(booking_id: int):
    booking = _owned_booking_or_404(booking_id)
    try:
        booking_svc.transition(
            booking,
            BookingStatus.NO_SHOW,
            actor="trade",
            description="Marked as no-show by trade",
            ip=request.remote_addr,
        )
    except DomainError as e:
        flash(e.message, "error")
    return redirect(url_for("dashboard.booking_detail", booking_id=booking.id))


@bp.route("/bookings/<int:booking_id>/cancel", methods=["POST"])
def cancel_by_trade(booking_id: int):
    booking = _owned_booking_or_404(booking_id)
    try:
        # Trade-initiated cancel always refunds the full deposit. Policy
        # protects the trade from customer flakiness; it shouldn't punish
        # the customer when the trade is the one backing out.
        full_refund = booking.deposit_pence if booking.is_paid else 0
        booking_svc.cancel(booking, actor="trade", ip=request.remote_addr)
        if full_refund > 0:
            from app.services import payments

            try:
                payments.refund_booking(booking, full_refund, reason="duplicate")
                flash(
                    f"Booking cancelled and {booking.deposit_display} refunded to the customer.",
                    "info",
                )
            except payments.RefundFailed:
                current_app.logger.exception(
                    "Refund failed on trade cancel for %s", booking.reference
                )
                flash(
                    "Booking cancelled but the refund didn't go through — "
                    "please issue it manually from your Stripe dashboard.",
                    "error",
                )
        else:
            flash("Booking cancelled.", "info")
    except DomainError as e:
        flash(e.message, "error")
    return redirect(url_for("dashboard.today"))


# ---------- Services ----------


@bp.route("/services")
def services():
    trade_services = sorted(current_user.services, key=lambda s: s.display_order)

    # Show templates if the trade hasn't added any services yet — quick onboarding
    from app.services.service_templates import templates_for

    show_templates = len(trade_services) == 0
    templates = templates_for(current_user.trade_type) if show_templates else []

    return render_template(
        "dashboard/services.html",
        services=trade_services,
        templates=templates,
        show_templates=show_templates,
    )


@bp.route("/services/from-template", methods=["POST"])
def services_from_template():
    """Bulk-add multiple services from the templates list. One submit, many services."""
    from app.services.service_templates import templates_for

    selected_keys = request.form.getlist("template")
    if not selected_keys:
        flash("Pick at least one to add.", "info")
        return redirect(url_for("dashboard.services"))

    templates = {t["key"]: t for t in templates_for(current_user.trade_type)}
    added = 0
    base_order = len(current_user.services)
    for i, key in enumerate(selected_keys):
        t = templates.get(key)
        if not t:
            continue
        s = Service(
            trade_id=current_user.id,
            name=t["name"],
            description=t["description"],
            icon=t["icon"],
            duration_minutes=t["duration_minutes"],
            deposit_pence=t["deposit_pence"],
            is_emergency=t.get("is_emergency", False),
            is_active=True,
            display_order=base_order + i,
        )
        db.session.add(s)
        added += 1

    db.session.commit()
    flash(f"Added {added} service{'s' if added != 1 else ''}. Edit any of them to fine-tune.", "success")
    return redirect(url_for("dashboard.services"))


@bp.route("/services/new", methods=["GET", "POST"])
def services_new():
    form = ServiceForm()
    if form.validate_on_submit():
        s = Service(
            trade_id=current_user.id,
            name=form.name.data.strip(),
            description=form.description.data or None,
            icon=form.icon.data or None,
            duration_minutes=form.duration_minutes.data,
            deposit_pence=form.deposit_pounds.data * 100,
            is_active=form.is_active.data,
            is_emergency=bool(form.is_emergency.data),
            display_order=len(current_user.services),
        )
        db.session.add(s)
        db.session.commit()
        flash("Service added.", "success")
        return redirect(url_for("dashboard.services"))
    return render_template("dashboard/service_form.html", form=form, service=None)


@bp.route("/services/<int:service_id>", methods=["GET", "POST"])
def services_edit(service_id: int):
    s = db.session.get(Service, service_id)
    if not s or s.trade_id != current_user.id:
        abort(404)
    form = ServiceForm(obj=s)
    if form.validate_on_submit():
        s.name = form.name.data.strip()
        s.description = form.description.data or None
        s.icon = form.icon.data or None
        s.duration_minutes = form.duration_minutes.data
        s.deposit_pence = form.deposit_pounds.data * 100
        s.is_active = form.is_active.data
        s.is_emergency = bool(form.is_emergency.data)
        db.session.commit()
        flash("Service updated.", "success")
        return redirect(url_for("dashboard.services"))
    # Populate pounds field from pence
    if request.method == "GET":
        form.deposit_pounds.data = s.deposit_pence // 100
    return render_template("dashboard/service_form.html", form=form, service=s)


# ---------- Availability ----------


@bp.route("/availability")
def availability():
    rules = sorted(current_user.availability_rules, key=lambda r: (r.weekday, r.start_time))
    form = AvailabilityRuleForm()
    return render_template("dashboard/availability.html", rules=rules, form=form)


@bp.route("/availability/rules/new", methods=["POST"])
def availability_add():
    form = AvailabilityRuleForm()
    if form.validate_on_submit():
        if form.start_time.data >= form.end_time.data:
            flash("End time must be after start time.", "error")
        else:
            db.session.add(
                AvailabilityRule(
                    trade_id=current_user.id,
                    weekday=form.weekday.data,
                    start_time=form.start_time.data,
                    end_time=form.end_time.data,
                )
            )
            db.session.commit()
            flash("Availability added.", "success")
    else:
        flash("Invalid input.", "error")
    return redirect(url_for("dashboard.availability"))


@bp.route("/availability/rules/<int:rule_id>/delete", methods=["POST"])
def availability_delete(rule_id: int):
    rule = db.session.get(AvailabilityRule, rule_id)
    if not rule or rule.trade_id != current_user.id:
        abort(404)
    db.session.delete(rule)
    db.session.commit()
    flash("Availability removed.", "info")
    return redirect(url_for("dashboard.availability"))


# ---------- Profile ----------


@bp.route("/profile", methods=["GET", "POST"])
def profile():
    form = ProfileForm(obj=current_user)
    if form.validate_on_submit():
        # Guard: don't publish unless Stripe is ready.
        wanted_publish = bool(form.is_published.data)
        if wanted_publish and not current_user.stripe_charges_enabled:
            flash(
                "Connect Stripe before publishing your booking page — "
                "customers can't book without a way to pay the deposit.",
                "error",
            )
            form.is_published.data = False
            return render_template("dashboard/profile.html", form=form)

        for field in (
            "business_name",
            "contact_name",
            "bio",
            "phone",
            "service_area",
            "credentials",
            "years_active",
            "cancellation_policy",
            "is_published",
        ):
            setattr(current_user, field, getattr(form, field).data)
        db.session.commit()
        flash("Profile saved.", "success")
        return redirect(url_for("dashboard.profile"))
    return render_template("dashboard/profile.html", form=form)


# ---------- Stripe onboarding (link-outs only here, logic in payments service) ----------


from app.extensions import csrf as _csrf  # noqa: E402  (used by csrf.exempt below)


@bp.route("/tracking/<token>/ping", methods=["POST"])
@_csrf.exempt
def tracking_ping(token: str):
    """Receive a location update from the trade's browser.

    Called every ~30s by JS on the booking detail page while the booking
    is 'on_the_way' and a tracking session is active.

    CSRF-exempt because it's called via fetch with a JSON body. Auth
    comes from login_required (via before_request, if any) plus the
    unguessable token in the URL.
    """
    from flask import jsonify, request as flask_request

    from app.services import tracking as tracking_svc

    session = tracking_svc.session_by_token(token)
    if not session:
        return jsonify(error="not found"), 404
    # Verify ownership — only the trade who owns the booking can ping.
    if session.booking.trade_id != current_user.id:
        return jsonify(error="forbidden"), 403

    payload = flask_request.get_json(silent=True) or {}
    try:
        lat = float(payload.get("lat"))
        lng = float(payload.get("lng"))
    except (TypeError, ValueError):
        return jsonify(error="invalid"), 400

    accuracy = payload.get("accuracy_m")
    heading = payload.get("heading_deg")
    try:
        tracking_svc.record_ping(
            session,
            latitude=lat,
            longitude=lng,
            accuracy_m=float(accuracy) if accuracy is not None else None,
            heading_deg=float(heading) if heading is not None else None,
        )
    except DomainError as e:
        return jsonify(error=str(e)), 410

    return jsonify(ok=True)


@bp.route("/stripe/connect", methods=["POST"])
def stripe_connect():
    from app.services import payments

    try:
        url = payments.create_account_link(current_user)
    except Exception:
        current_app.logger.exception("Stripe account link failed")
        flash("We couldn't open Stripe onboarding. Try again shortly.", "error")
        return redirect(url_for("dashboard.profile"))
    return redirect(url)


@bp.route("/stripe/return")
def stripe_return():
    from app.services import payments

    payments.refresh_trade_account_status(current_user)
    db.session.commit()
    flash("Stripe onboarding updated.", "success")
    return redirect(url_for("dashboard.profile"))


# ---------- Helpers ----------


def _list_bookings(trade_id: int, start_utc, end_utc):
    return list(
        db.session.execute(
            select(Booking)
            .where(
                and_(
                    Booking.trade_id == trade_id,
                    Booking.start_at >= start_utc,
                    Booking.start_at < end_utc,
                    Booking.status != BookingStatus.PENDING_PAYMENT,
                )
            )
            .order_by(Booking.start_at)
        ).scalars()
    )


def _owned_booking_or_404(booking_id: int) -> Booking:
    booking = db.session.get(Booking, booking_id)
    if not booking or booking.trade_id != current_user.id:
        abort(404)
    return booking


def _next_status(status: BookingStatus) -> BookingStatus | None:
    flow = [
        BookingStatus.BOOKED,
        BookingStatus.ON_THE_WAY,
        BookingStatus.ARRIVED,
        BookingStatus.IN_PROGRESS,
        BookingStatus.COMPLETE,
    ]
    if status not in flow:
        return None
    idx = flow.index(status)
    if idx + 1 >= len(flow):
        return None
    return flow[idx + 1]
