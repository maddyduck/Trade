"""Flask extensions, initialised once and imported from anywhere.

Each extension is created here without an app. The factory in app/__init__.py
binds them to the app via init_app(). This is the standard Flask application-
factory pattern and is what allows tests to spin up a fresh app per session.
"""
from __future__ import annotations

from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from itsdangerous import URLSafeTimedSerializer

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()

# Signed-token serializer for magic links. Bound to SECRET_KEY in create_app().
# A module-level variable so views can import it; we mutate .secret_key on init.
_signer: URLSafeTimedSerializer | None = None


def get_signer() -> URLSafeTimedSerializer:
    if _signer is None:
        raise RuntimeError("Signer not initialised; create_app must run first")
    return _signer


def set_signer(secret_key: str) -> None:
    global _signer
    _signer = URLSafeTimedSerializer(secret_key, salt="sorted-magic-link-v1")
