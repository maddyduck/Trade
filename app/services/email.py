"""Email sending. Console backend in dev, SMTP in prod, memory in tests.

Templates live under templates/emails/ and are rendered with Jinja.
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from flask import current_app, render_template

logger = logging.getLogger(__name__)

# For tests: list of (to, subject, body) tuples
_memory_outbox: list[tuple[str, str, str]] = []


def memory_outbox() -> list[tuple[str, str, str]]:
    return _memory_outbox


def clear_memory_outbox() -> None:
    _memory_outbox.clear()


def send_email(to: str, subject: str, template: str, **context) -> None:
    """Render `template.txt` (and optionally `template.html`) and send."""
    text_body = render_template(f"emails/{template}.txt", **context)
    try:
        html_body = render_template(f"emails/{template}.html", **context)
    except Exception:
        html_body = None

    backend = current_app.config.get("MAIL_BACKEND", "console")
    from_addr = current_app.config.get("MAIL_FROM", "Sorted <bookings@sorted.ni>")

    if backend == "console":
        logger.info("=== EMAIL (console) ===")
        logger.info("From: %s", from_addr)
        logger.info("To: %s", to)
        logger.info("Subject: %s", subject)
        logger.info("\n%s\n", text_body)
        logger.info("=======================")
        return

    if backend == "memory":
        _memory_outbox.append((to, subject, text_body))
        return

    if backend == "smtp":
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = to
        msg.set_content(text_body)
        if html_body:
            msg.add_alternative(html_body, subtype="html")

        host = current_app.config["SMTP_HOST"]
        port = current_app.config["SMTP_PORT"]
        user = current_app.config["SMTP_USER"]
        password = current_app.config["SMTP_PASS"]
        try:
            with smtplib.SMTP(host, port, timeout=10) as s:
                s.starttls()
                if user:
                    s.login(user, password)
                s.send_message(msg)
            logger.info("Email sent to %s: %s", to, subject)
        except (smtplib.SMTPException, OSError) as e:
            # Log + report to Sentry but don't crash the user request.
            # Important emails (booking confirmations) can be re-sent via
            # `flask resend-confirmation <reference>` if needed.
            logger.error("SMTP send failed for %s: %s", to, e)
            try:
                import sentry_sdk

                sentry_sdk.capture_exception(e)
            except ImportError:
                pass
        return

    raise RuntimeError(f"Unknown MAIL_BACKEND: {backend}")
