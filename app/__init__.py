"""Flask application factory.

Pattern: create_app(config) returns a fully wired Flask instance.
Import-time side effects are kept minimal so tests can create/destroy apps.
"""
from __future__ import annotations

import logging

from flask import Flask, render_template
from flask_wtf.csrf import CSRFError

from app.extensions import csrf, db, login_manager, migrate, set_signer
from config import BaseConfig, get_config


def create_app(config_class: type[BaseConfig] | None = None) -> Flask:
    config_class = config_class or get_config()
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object(config_class)

    if hasattr(config_class, "validate"):
        config_class.validate()

    _setup_logging(app)
    _setup_sentry(app)

    # Extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    set_signer(app.config["SECRET_KEY"])

    # Flask-Login
    from app.models import Trade

    login_manager.login_view = "auth.login"
    login_manager.login_message_category = "info"

    @login_manager.user_loader
    def load_user(user_id: str):
        return db.session.get(Trade, int(user_id))

    # Blueprints
    from app.api.webhooks import bp as webhooks_bp
    from app.auth.routes import bp as auth_bp
    from app.dashboard.invoice_routes import bp as invoices_bp
    from app.dashboard.routes import bp as dashboard_bp
    from app.health import bp as health_bp
    from app.public.invoice_routes import bp as public_invoices_bp
    from app.public.routes import bp as public_bp
    from app.public.tracking_routes import bp as tracking_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(public_invoices_bp, url_prefix="/invoices")
    app.register_blueprint(tracking_bp, url_prefix="/track")
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(dashboard_bp, url_prefix="/dashboard")
    app.register_blueprint(invoices_bp, url_prefix="/dashboard/invoices")
    app.register_blueprint(webhooks_bp, url_prefix="/webhooks")
    app.register_blueprint(health_bp)
    csrf.exempt(webhooks_bp)  # webhooks are signed by Stripe, not our CSRF

    # Local-storage serve route (dev only — does nothing in s3 backend)
    from app.services.storage import serve_local_route

    serve_local_route(app)

    # Template globals & filters
    _register_template_helpers(app)

    # Error handlers
    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        app.logger.exception("500 error: %s", e)
        return render_template("errors/500.html"), 500

    @app.errorhandler(CSRFError)
    def csrf_error(e):
        return render_template("errors/csrf.html", reason=e.description), 400

    # CLI
    from app.cli import register_cli

    register_cli(app)

    return app


def _setup_logging(app: Flask) -> None:
    level = logging.DEBUG if app.debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _setup_sentry(app: Flask) -> None:
    """Initialise Sentry if a DSN is configured.

    Silently no-ops if SENTRY_DSN isn't set (so dev/test don't need it).
    Configure SENTRY_DSN in your prod environment to start collecting errors.
    """
    dsn = app.config.get("SENTRY_DSN")
    if not dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration

        sentry_sdk.init(
            dsn=dsn,
            integrations=[FlaskIntegration()],
            traces_sample_rate=app.config.get("SENTRY_TRACES_SAMPLE_RATE", 0.1),
            environment=app.config.get("ENV_NAME", "production"),
            release=app.config.get("APP_RELEASE"),
            send_default_pii=False,  # don't send PII by default; review before enabling
        )
        app.logger.info("Sentry initialised")
    except ImportError:
        app.logger.warning(
            "SENTRY_DSN is set but sentry-sdk is not installed. "
            "Run `pip install sentry-sdk[flask]` to enable."
        )


def _register_template_helpers(app: Flask) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(app.config.get("APP_TIMEZONE", "Europe/London"))

    @app.template_filter("pence_to_pounds")
    def pence_to_pounds(p: int | None) -> str:
        if p is None:
            return "—"
        if p % 100 == 0:
            return f"£{p // 100}"
        return f"£{p / 100:.2f}"

    @app.template_filter("local_dt")
    def local_dt(dt: datetime | None, fmt: str = "%a %d %b, %H:%M") -> str:
        if not dt:
            return ""
        return dt.astimezone(tz).strftime(fmt)

    @app.template_filter("local_time")
    def local_time(dt: datetime | None) -> str:
        return dt.astimezone(tz).strftime("%H:%M") if dt else ""

    @app.template_filter("local_date")
    def local_date(dt: datetime | None, fmt: str = "%a %d %b") -> str:
        return dt.astimezone(tz).strftime(fmt) if dt else ""

    @app.context_processor
    def inject_globals():
        from app.services.customers import is_returning_customer, prior_booking_count

        return {
            "APP_NAME": "Sorted",
            "APP_TAGLINE": "Booked in. Sorted.",
            "NOW": datetime.now(tz),
            "is_returning_customer": is_returning_customer,
            "prior_booking_count": prior_booking_count,
        }
