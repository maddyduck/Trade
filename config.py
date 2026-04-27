"""Application configuration.

Three environments: development, testing, production. One class each, all
inheriting from BaseConfig. Values come from environment variables with
sensible dev defaults; prod must set SECRET_KEY and DATABASE_URL explicitly.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


class BaseConfig:
    # Core
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-key-do-not-use-in-prod")
    APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:5000")
    APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "Europe/London")

    # Database
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://postgres:dev@localhost:5432/sorted",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,  # survive idle disconnects on Render
        "pool_recycle": 300,
    }

    # Session / security
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_HTTPONLY = True
    WTF_CSRF_TIME_LIMIT = 3600  # 1 hour

    # Booking rules
    BOOKING_HORIZON_DAYS = 14
    DEFAULT_SLOT_MINUTES = 30
    DEFAULT_BUFFER_MINUTES = 0
    MAGIC_LINK_MAX_AGE_SECONDS = 60 * 60 * 24  # 24 hrs

    # Stripe
    STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
    STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
    STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    STRIPE_CONNECT_CLIENT_ID = os.environ.get("STRIPE_CONNECT_CLIENT_ID", "")
    PLATFORM_FEE_PCT = 0  # MVP: no platform take. Revisit when commercial model settled.

    # Email
    MAIL_BACKEND = os.environ.get("MAIL_BACKEND", "console")
    MAIL_FROM = os.environ.get("MAIL_FROM", "Sorted <bookings@sorted.ni>")
    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASS = os.environ.get("SMTP_PASS", "")

    # Twilio (off by default)
    TWILIO_ENABLED = os.environ.get("TWILIO_ENABLED", "false").lower() == "true"
    TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
    TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER", "")

    # Sentry (off by default)
    SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
    SENTRY_TRACES_SAMPLE_RATE = float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1"))
    APP_RELEASE = os.environ.get("APP_RELEASE", "dev")
    ENV_NAME = os.environ.get("ENV_NAME", "development")

    # File storage (photos)
    # Backend: 'local' (writes to STORAGE_LOCAL_DIR) or 's3' (writes to S3-compatible).
    # Local is for dev; production should use s3 (works with AWS S3, R2, etc).
    STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local")
    STORAGE_LOCAL_DIR = os.environ.get(
        "STORAGE_LOCAL_DIR", str(BASE_DIR / "instance" / "uploads")
    )
    STORAGE_S3_BUCKET = os.environ.get("STORAGE_S3_BUCKET", "")
    STORAGE_S3_REGION = os.environ.get("STORAGE_S3_REGION", "")
    STORAGE_S3_ENDPOINT = os.environ.get("STORAGE_S3_ENDPOINT", "")  # for R2/MinIO
    STORAGE_S3_PUBLIC_BASE = os.environ.get("STORAGE_S3_PUBLIC_BASE", "")
    STORAGE_S3_ACCESS_KEY = os.environ.get("STORAGE_S3_ACCESS_KEY", "")
    STORAGE_S3_SECRET_KEY = os.environ.get("STORAGE_S3_SECRET_KEY", "")
    MAX_PHOTO_BYTES = int(os.environ.get("MAX_PHOTO_BYTES", str(10 * 1024 * 1024)))
    MAX_PHOTOS_PER_BOOKING = int(os.environ.get("MAX_PHOTOS_PER_BOOKING", "3"))

    # Live tracking link (Uber-ish "on the way" feature)
    TRACKING_LINK_TTL_MINUTES = int(os.environ.get("TRACKING_LINK_TTL_MINUTES", "60"))


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    SESSION_COOKIE_SECURE = False


class TestingConfig(BaseConfig):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-key"
    MAIL_BACKEND = "memory"
    STRIPE_SECRET_KEY = "sk_test_dummy"
    STRIPE_WEBHOOK_SECRET = "whsec_dummy"


class ProductionConfig(BaseConfig):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True

    @classmethod
    def validate(cls) -> None:
        required = ["SECRET_KEY", "DATABASE_URL", "STRIPE_SECRET_KEY"]
        missing = [k for k in required if not os.environ.get(k)]
        if missing:
            raise RuntimeError(f"Missing required env vars in production: {missing}")


CONFIGS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config() -> type[BaseConfig]:
    env = os.environ.get("FLASK_ENV", "development")
    return CONFIGS.get(env, DevelopmentConfig)
