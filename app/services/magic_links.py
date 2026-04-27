"""Magic links for customer-side booking management.

A magic link is a signed URL that gives short-lived, booking-scoped access
to the manage-booking view — no account required. Used for:
  - the confirmation email link,
  - the 'lost my reference' recovery flow.

Tokens are stateless (no DB row). Revocation is by expiry.
"""
from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired

from app.extensions import get_signer


def make_token(booking_id: int) -> str:
    return get_signer().dumps({"b": booking_id})


def read_token(token: str, max_age_seconds: int) -> int | None:
    try:
        data = get_signer().loads(token, max_age=max_age_seconds)
        return int(data["b"])
    except SignatureExpired:
        return None
    except (BadSignature, KeyError, ValueError, TypeError):
        return None
