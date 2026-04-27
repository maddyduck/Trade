"""Tests for app.services.magic_links."""
from __future__ import annotations

import time

from app.services.magic_links import make_token, read_token


def test_token_roundtrip(app):
    token = make_token(42)
    assert read_token(token, max_age_seconds=60) == 42


def test_tampered_token_rejected(app):
    token = make_token(42)
    bad = token[:-4] + "ZZZZ"
    assert read_token(bad, max_age_seconds=60) is None


def test_expired_token_rejected(app):
    token = make_token(42)
    time.sleep(2)
    assert read_token(token, max_age_seconds=1) is None
