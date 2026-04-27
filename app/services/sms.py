"""SMS notifications via Twilio.

Best-effort: if Twilio isn't configured (TWILIO_ENABLED=false or missing
credentials), all calls become no-ops with a debug log. This way:

  - Dev environments don't need Twilio credentials.
  - Tests don't accidentally send texts.
  - In prod, if Twilio briefly goes down, customer texts fail silently
    rather than breaking the booking flow.

For tests, we expose a memory_outbox like the email module.
"""
from __future__ import annotations

import logging

from flask import current_app

logger = logging.getLogger(__name__)

_memory_outbox: list[tuple[str, str]] = []


def memory_outbox() -> list[tuple[str, str]]:
    return _memory_outbox


def clear_memory_outbox() -> None:
    _memory_outbox.clear()


def _normalise_uk_number(phone: str) -> str | None:
    """Best-effort normalise to E.164 for UK numbers.

    '07712 445 892' -> '+447712445892'
    '+447712445892' -> '+447712445892'
    Anything else -> as-is (Twilio will validate).
    """
    if not phone:
        return None
    cleaned = "".join(c for c in phone if c.isdigit() or c == "+")
    if cleaned.startswith("+"):
        return cleaned
    if cleaned.startswith("00"):
        return "+" + cleaned[2:]
    if cleaned.startswith("0") and len(cleaned) == 11:
        return "+44" + cleaned[1:]
    return cleaned if cleaned else None


def send_sms(to: str, body: str) -> bool:
    """Send a single SMS. Returns True if sent or queued, False on failure.

    Body is truncated to 320 chars (2 SMS segments) to keep cost predictable.
    """
    body = (body or "").strip()
    if not body:
        return False
    if len(body) > 320:
        body = body[:317] + "…"

    to_norm = _normalise_uk_number(to)
    if not to_norm:
        logger.debug("SMS skipped: no/invalid number")
        return False

    backend = current_app.config.get("SMS_BACKEND", "auto")
    if backend == "memory" or current_app.config.get("TESTING"):
        _memory_outbox.append((to_norm, body))
        return True

    if not current_app.config.get("TWILIO_ENABLED"):
        logger.info("SMS not sent (Twilio disabled). To: %s, Body: %s", to_norm, body)
        return False

    sid = current_app.config.get("TWILIO_ACCOUNT_SID")
    token = current_app.config.get("TWILIO_AUTH_TOKEN")
    from_number = current_app.config.get("TWILIO_FROM_NUMBER")
    if not (sid and token and from_number):
        logger.warning("SMS not sent: Twilio enabled but credentials missing")
        return False

    try:
        from twilio.rest import Client

        client = Client(sid, token)
        client.messages.create(body=body, from_=from_number, to=to_norm)
        logger.info("SMS sent to %s (%d chars)", to_norm, len(body))
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("SMS send failed for %s: %s", to_norm, e)
        try:
            import sentry_sdk

            sentry_sdk.capture_exception(e)
        except ImportError:
            pass
        return False
