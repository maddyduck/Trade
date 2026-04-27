"""Customer-facing routes.

URL map:
  /                         → marketing home (placeholder)
  /find                     → find-my-booking form
  /find/sent                → magic link sent
  /manage/<token>           → manage booking via magic link
  /<slug>/                  → trade's public page
  /<slug>/book/<service_id> → pick slot
  /<slug>/book/<service_id>/details → customer details form
  /<slug>/book/<service_id>/confirm → review & pay
  /<slug>/booked/<ref>      → post-payment confirmation (comes back from Stripe)
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import select

from app.extensions import db
from app.models import Booking, BookingStatus, Service, Trade
from app.public.forms import BookingForm, FindBookingForm, MagicLinkForm
from app.services import bookings as booking_svc
from app.services.errors import DomainError, SlotUnavailable
from app.services.magic_links import make_token, read_token
from app.services.slots import generate_slots

bp = Blueprint("public", __name__)


# ---------- Helpers ----------


def _get_trade_or_404(slug: str) -> Trade:
    trade = db.session.execute(
        select(Trade).where(Trade.slug == slug, Trade.is_active.is_(True))
    ).scalar_one_or_none()
    if not trade or not trade.is_published:
        abort(404)
    return trade


def _session_key(slug: str, service_id: int) -> str:
    return f"booking_draft:{slug}:{service_id}"


# ---------- Marketing home (placeholder) ----------


@bp.route("/")
def home():
    return render_template("public/home.html")


# ---------- Find / manage ----------


@bp.route("/find", methods=["GET", "POST"])
def find_booking():
    form = FindBookingForm()
    if form.validate_on_submit():
        booking = booking_svc.find_for_customer_lookup(
            reference=form.reference.data,
            contact=form.contact.data,
        )
        if booking:
            token = make_token(booking.id)
            return redirect(url_for("public.manage_booking", token=token))
        flash("We couldn't find a booking matching those details.", "error")
    return render_template("public/find.html", form=form)


@bp.route("/find/lost", methods=["GET", "POST"])
def lost_reference():
    form = MagicLinkForm()
    if form.validate_on_submit():
        contact = form.contact.data.strip().lower()
        # Security: always return the same page whether or not we found a booking.
        matches = booking_svc.find_bookings_by_contact(contact)
        if matches:
            from app.services.email import send_email

            # Pick the most recent active booking.
            active = [b for b in matches if not b.is_terminal]
            chosen = active[0] if active else matches[-1]
            token = make_token(chosen.id)
            link = url_for("public.manage_booking", token=token, _external=True)
            try:
                send_email(
                    to=chosen.customer_email,
                    subject="Your Sorted booking link",
                    template="magic_link",
                    link=link,
                    booking=chosen,
                )
            except Exception:
                current_app.logger.exception("Failed sending magic link email")
        return redirect(url_for("public.magic_link_sent", contact=contact))
    return render_template("public/lost.html", form=form)


@bp.route("/find/sent")
def magic_link_sent():
    contact = request.args.get("contact", "")
    return render_template("public/magic_sent.html", contact=contact)


@bp.route("/manage/<token>")
def manage_booking(token: str):
    booking_id = read_token(
        token,
        max_age_seconds=current_app.config["MAGIC_LINK_MAX_AGE_SECONDS"],
    )
    if booking_id is None:
        return render_template("public/magic_expired.html"), 410
    booking = db.session.get(Booking, booking_id)
    if not booking:
        abort(404)
    return render_template("public/manage.html", booking=booking, token=token)


@bp.route("/manage/<token>/photos", methods=["POST"])
def upload_photo(token: str):
    booking_id = read_token(
        token, max_age_seconds=current_app.config["MAGIC_LINK_MAX_AGE_SECONDS"]
    )
    if booking_id is None:
        abort(410)
    booking = db.session.get(Booking, booking_id)
    if not booking:
        abort(404)

    max_photos = current_app.config.get("MAX_PHOTOS_PER_BOOKING", 3)
    existing = list(booking.photos)
    if len(existing) >= max_photos:
        flash(f"Up to {max_photos} photos allowed.", "error")
        return redirect(url_for("public.manage_booking", token=token))

    if "photo" not in request.files:
        flash("No file uploaded.", "error")
        return redirect(url_for("public.manage_booking", token=token))

    file = request.files["photo"]
    if not file or not file.filename:
        flash("No file uploaded.", "error")
        return redirect(url_for("public.manage_booking", token=token))

    from app.models import BookingPhoto
    from app.services import storage as storage_svc

    try:
        result = storage_svc.store_image(file.stream, file.mimetype or "image/jpeg")
    except storage_svc.StorageError as e:
        flash(f"Couldn't accept that photo: {e}", "error")
        return redirect(url_for("public.manage_booking", token=token))

    photo = BookingPhoto(
        booking_id=booking.id,
        storage_key=result["storage_key"],
        public_url=result["public_url"],
        thumbnail_url=result["thumbnail_url"],
        content_type=result["content_type"],
        width=result["width"],
        height=result["height"],
        size_bytes=result["size_bytes"],
        position=len(existing),
    )
    db.session.add(photo)
    db.session.commit()
    flash("Photo uploaded.", "success")
    return redirect(url_for("public.manage_booking", token=token))


@bp.route("/manage/<token>/photos/<int:photo_id>/delete", methods=["POST"])
def delete_photo(token: str, photo_id: int):
    booking_id = read_token(
        token, max_age_seconds=current_app.config["MAGIC_LINK_MAX_AGE_SECONDS"]
    )
    if booking_id is None:
        abort(410)
    booking = db.session.get(Booking, booking_id)
    if not booking:
        abort(404)

    from app.models import BookingPhoto
    from app.services import storage as storage_svc

    photo = db.session.get(BookingPhoto, photo_id)
    if not photo or photo.booking_id != booking.id:
        abort(404)

    storage_svc.delete_object(photo.storage_key)
    db.session.delete(photo)
    db.session.commit()
    flash("Photo removed.", "info")
    return redirect(url_for("public.manage_booking", token=token))


@bp.route("/manage/<token>/cancel", methods=["POST"])
def cancel_booking(token: str):
    booking_id = read_token(
        token, max_age_seconds=current_app.config["MAGIC_LINK_MAX_AGE_SECONDS"]
    )
    if booking_id is None:
        abort(410)
    booking = db.session.get(Booking, booking_id)
    if not booking:
        abort(404)
    try:
        _, refund_pence = booking_svc.cancel(
            booking,
            actor="customer",
            reason="Cancelled by customer via magic link",
            ip=request.remote_addr,
        )
        _issue_refund_if_any(booking, refund_pence)
        _flash_cancellation_result(booking, refund_pence)
    except DomainError as e:
        flash(e.message, "error")
    return redirect(url_for("public.manage_booking", token=token))


def _issue_refund_if_any(booking: Booking, refund_pence: int) -> None:
    """Trigger Stripe refund for a cancelled booking. Logs rather than raises
    on failure — the booking is already cancelled; refund issues need humans."""
    if refund_pence <= 0 or not booking.stripe_payment_intent_id:
        return
    from app.services import payments

    try:
        payments.refund_booking(booking, refund_pence)
    except payments.RefundFailed:
        current_app.logger.exception(
            "Refund failed for cancelled booking %s — manual follow-up needed",
            booking.reference,
        )


def _flash_cancellation_result(booking: Booking, refund_pence: int) -> None:
    if refund_pence == 0:
        flash(
            "Your booking has been cancelled. Per the cancellation policy, "
            "the deposit is retained.",
            "info",
        )
    elif refund_pence >= booking.deposit_pence:
        flash(
            f"Your booking has been cancelled and your {booking.deposit_display} "
            "deposit will be refunded (usually 5–10 days).",
            "success",
        )
    else:
        partial = f"£{refund_pence / 100:.2f}"
        flash(
            f"Your booking has been cancelled. A partial refund of {partial} "
            "will be issued (usually 5–10 days).",
            "info",
        )


# ---------- Trade public page ----------


@bp.route("/<slug>/")
def trade_page(slug: str):
    trade = _get_trade_or_404(slug)
    active_services = sorted(
        [s for s in trade.services if s.is_active],
        key=lambda s: (not s.is_emergency, s.display_order),
    )

    # Honest response time — derived from real data when available.
    from app.services.response_time import response_time_label_for

    response_time_label = response_time_label_for(trade)

    return render_template(
        "public/trade.html",
        trade=trade,
        services=active_services,
        can_book=trade.can_accept_bookings,
        response_time_label=response_time_label,
    )


# ---------- Booking flow (3 steps + review) ----------


@bp.route("/<slug>/book/<int:service_id>", methods=["GET"])
def book_pick_slot(slug: str, service_id: int):
    trade = _get_trade_or_404(slug)
    if not trade.can_accept_bookings:
        flash(
            "This trade isn't taking online bookings right now. "
            "Try calling them directly.",
            "info",
        )
        return redirect(url_for("public.trade_page", slug=slug))
    service = _get_service_or_404(trade, service_id)

    tz = ZoneInfo(current_app.config["APP_TIMEZONE"])
    today_local = datetime.now(tz).date()
    horizon = current_app.config["BOOKING_HORIZON_DAYS"]
    day_slots = generate_slots(trade, service, from_date=today_local, days=horizon)

    return render_template(
        "public/book_slot.html", trade=trade, service=service, day_slots=day_slots
    )


@bp.route("/<slug>/book/<int:service_id>/details", methods=["GET", "POST"])
def book_details(slug: str, service_id: int):
    trade = _get_trade_or_404(slug)
    if not trade.can_accept_bookings:
        flash(
            "This trade isn't taking online bookings right now. "
            "Try calling them directly.",
            "info",
        )
        return redirect(url_for("public.trade_page", slug=slug))
    service = _get_service_or_404(trade, service_id)

    start_at_iso = request.values.get("start_at") or session.get(
        _session_key(slug, service_id), {}
    ).get("start_at_iso")

    if not start_at_iso:
        return redirect(url_for("public.book_pick_slot", slug=slug, service_id=service_id))

    form = BookingForm(service_id=str(service_id), start_at_iso=start_at_iso)

    if form.validate_on_submit():
        # Persist draft in session and advance to review.
        session[_session_key(slug, service_id)] = {
            "start_at_iso": form.start_at_iso.data,
            "customer_name": form.customer_name.data,
            "customer_phone": form.customer_phone.data,
            "customer_email": form.customer_email.data,
            "customer_postcode": form.customer_postcode.data,
            "customer_address": form.customer_address.data,
            "job_notes": form.job_notes.data,
        }
        return redirect(url_for("public.book_review", slug=slug, service_id=service_id))

    start_at_utc = datetime.fromisoformat(start_at_iso)
    return render_template(
        "public/book_details.html",
        trade=trade,
        service=service,
        start_at=start_at_utc,
        form=form,
    )


@bp.route("/<slug>/book/<int:service_id>/review", methods=["GET", "POST"])
def book_review(slug: str, service_id: int):
    trade = _get_trade_or_404(slug)
    if not trade.can_accept_bookings:
        flash(
            "This trade isn't taking online bookings right now. "
            "Try calling them directly.",
            "info",
        )
        return redirect(url_for("public.trade_page", slug=slug))
    service = _get_service_or_404(trade, service_id)

    draft = session.get(_session_key(slug, service_id))
    if not draft:
        return redirect(url_for("public.book_pick_slot", slug=slug, service_id=service_id))

    start_at_utc = datetime.fromisoformat(draft["start_at_iso"])

    if request.method == "POST":
        try:
            booking = booking_svc.create_pending_booking(
                trade=trade,
                service=service,
                start_at_utc=start_at_utc,
                customer_name=draft["customer_name"],
                customer_phone=draft["customer_phone"],
                customer_email=draft["customer_email"],
                customer_postcode=draft["customer_postcode"],
                customer_address=draft.get("customer_address"),
                job_notes=draft["job_notes"],
                ip=request.remote_addr,
            )
        except SlotUnavailable as e:
            flash(e.message, "error")
            return redirect(
                url_for("public.book_pick_slot", slug=slug, service_id=service_id)
            )
        except DomainError as e:
            flash(e.message, "error")
            return redirect(url_for("public.trade_page", slug=slug))

        # Clear the draft; the booking now owns the data.
        session.pop(_session_key(slug, service_id), None)

        # Hand off to Stripe.
        from app.services import payments

        try:
            url = payments.create_checkout_session(booking)
        except payments.TradeStripeNotReady as e:
            current_app.logger.warning(
                "Booking %s hit trade-not-ready at checkout", booking.reference
            )
            flash(e.message, "error")
            return redirect(url_for("public.trade_page", slug=slug))
        except payments.CheckoutCreationFailed as e:
            current_app.logger.exception("Stripe checkout failed")
            flash(e.message, "error")
            return redirect(
                url_for("public.book_pick_slot", slug=slug, service_id=service_id)
            )
        except Exception:
            current_app.logger.exception("Unexpected Stripe error")
            flash(
                "We couldn't start the payment. Please try again in a moment.",
                "error",
            )
            return redirect(
                url_for("public.book_pick_slot", slug=slug, service_id=service_id)
            )

        return redirect(url)

    return render_template(
        "public/book_review.html",
        trade=trade,
        service=service,
        start_at=start_at_utc,
        draft=draft,
    )


@bp.route("/<slug>/booked/<ref>")
def booked_confirmation(slug: str, ref: str):
    trade = _get_trade_or_404(slug)
    booking = db.session.execute(
        select(Booking).where(Booking.reference == ref.upper(), Booking.trade_id == trade.id)
    ).scalar_one_or_none()
    if not booking:
        abort(404)

    # Safety net: if the webhook hasn't arrived yet, reconcile from Stripe.
    # Cheap — just a couple of API calls, and only when status is still
    # pending_payment. If it's already BOOKED, skip.
    if booking.status == BookingStatus.PENDING_PAYMENT:
        try:
            from app.services import payments

            payments.reconcile_booking(booking)
        except Exception:
            current_app.logger.exception(
                "Reconciliation failed on confirmation view for %s", booking.reference
            )

    # Issue the magic-link token so the confirmation page has a manage link.
    token = make_token(booking.id)
    return render_template(
        "public/booked.html", trade=trade, booking=booking, manage_token=token
    )


# ---------- Stripe return handler ----------


@bp.route("/<slug>/stripe/return")
def stripe_return(slug: str):
    """Stripe redirects here after checkout (success or cancel).

    The webhook is the source of truth for payment state; this route just
    routes the user to the right next page.
    """
    trade = _get_trade_or_404(slug)
    ref = request.args.get("ref")
    if not ref:
        return redirect(url_for("public.trade_page", slug=slug))
    if request.args.get("status") == "cancel":
        flash("Payment cancelled — your slot hasn't been confirmed.", "info")
        return redirect(url_for("public.trade_page", slug=slug))
    return redirect(url_for("public.booked_confirmation", slug=slug, ref=ref))


# ---------- Internal helpers ----------


def _get_service_or_404(trade: Trade, service_id: int) -> Service:
    service = db.session.get(Service, service_id)
    if not service or service.trade_id != trade.id or not service.is_active:
        abort(404)
    return service
