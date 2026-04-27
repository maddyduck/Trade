"""Password reset tokens for trades.

Same pattern as magic links but a separate signer salt so a leaked
booking-management token can't be reused as a password reset.
"""
from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.extensions import get_signer


def _password_reset_signer() -> URLSafeTimedSerializer:
    # Reuse the secret key but with a different salt for namespace separation.
    base = get_signer()
    return URLSafeTimedSerializer(base.secret_key, salt="sorted-password-reset-v1")


def make_reset_token(trade_id: int) -> str:
    return _password_reset_signer().dumps({"t": trade_id})


def read_reset_token(token: str, max_age_seconds: int = 3600) -> int | None:
    try:
        data = _password_reset_signer().loads(token, max_age=max_age_seconds)
        return int(data["t"])
    except SignatureExpired:
        return None
    except (BadSignature, KeyError, ValueError, TypeError):
        return None
