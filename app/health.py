"""Health check endpoints.

- `/healthz` — liveness, just returns 200 ok. For load balancers.
- `/readyz`  — readiness, also checks DB connectivity. For deploy validation.
"""
from __future__ import annotations

from flask import Blueprint, jsonify
from sqlalchemy import text

from app.extensions import db

bp = Blueprint("health", __name__)


@bp.route("/healthz")
def liveness():
    return jsonify(status="ok"), 200


@bp.route("/readyz")
def readiness():
    """Confirm the app can reach its dependencies."""
    checks: dict[str, str] = {}
    overall_ok = True

    # Database
    try:
        db.session.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as e:  # noqa: BLE001
        checks["db"] = f"error: {type(e).__name__}"
        overall_ok = False

    return jsonify(status="ok" if overall_ok else "degraded", checks=checks), (
        200 if overall_ok else 503
    )
