"""Public-facing live tracking page.

URL: /track/<public_token>
The token is unguessable and only sent to the customer in their notification
when the trade taps "On the way" + "Share live location."

We render the customer-facing map and serve the JSON ping endpoint that the
map polls every 30s.
"""
from __future__ import annotations

from datetime import UTC, datetime

from flask import Blueprint, abort, jsonify, render_template

from app.services import tracking as track_svc

bp = Blueprint("public_tracking", __name__, template_folder="../templates/public")


@bp.route("/<token>")
def show(token: str):
    session = track_svc.session_by_token(token)
    if not session:
        abort(404)
    booking = session.booking
    trade = booking.trade
    return render_template(
        "public/tracking.html",
        session=session,
        booking=booking,
        trade=trade,
    )


@bp.route("/<token>/ping.json")
def ping_json(token: str):
    """Return latest known position as JSON for the customer's map.

    Status:
      'active'  - has a recent ping, return location
      'waiting' - session active but no ping yet
      'arrived' - trade has arrived
      'expired' - session expired
      'stopped' - trade ended manually
      'unknown' - session not found
    """
    session = track_svc.session_by_token(token)
    if not session:
        return jsonify(status="unknown"), 404

    if session.status.value == "arrived":
        return jsonify(status="arrived")
    if session.status.value == "expired":
        return jsonify(status="expired")
    if session.status.value == "stopped":
        return jsonify(status="stopped")

    # ACTIVE
    if session.expires_at < datetime.now(UTC):
        return jsonify(status="expired")

    p = track_svc.latest_ping(session)
    if not p:
        return jsonify(
            status="waiting",
            eta_minutes=session.eta_minutes,
            expires_at=session.expires_at.isoformat(),
        )

    return jsonify(
        status="active",
        lat=p.latitude,
        lng=p.longitude,
        accuracy_m=p.accuracy_m,
        heading_deg=p.heading_deg,
        last_seen=p.created_at.isoformat(),
        eta_minutes=session.eta_minutes,
        expires_at=session.expires_at.isoformat(),
    )
