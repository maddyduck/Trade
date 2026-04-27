"""Booking reference generator.

Format: AAA-NNNN-XX
  AAA  = 3 letters derived from the trade's slug
  NNNN = 4 pseudo-random digits
  XX   = 2 alphanumeric chars (excluding confusing chars)

Example: MMK-4827-3F

Not cryptographically secure — the reference is *paired* with customer
contact details for lookup, so guessing alone doesn't reveal anything.
"""
from __future__ import annotations

import secrets
import string

# Excludes I, O, 0, 1 to avoid confusion.
_SUFFIX_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _trade_prefix(slug: str) -> str:
    letters = [c for c in slug.upper() if c.isalpha()]
    if len(letters) >= 3:
        # First letter + two consonants if possible, else first three
        out = letters[0]
        for c in letters[1:]:
            if c not in "AEIOU" and len(out) < 3:
                out += c
        while len(out) < 3 and len(letters) > len(out):
            out += letters[len(out)]
        return (out + "XXX")[:3]
    return (("".join(letters) + "XXX"))[:3]


def generate_reference(trade_slug: str) -> str:
    prefix = _trade_prefix(trade_slug)
    digits = "".join(secrets.choice(string.digits) for _ in range(4))
    suffix = "".join(secrets.choice(_SUFFIX_ALPHABET) for _ in range(2))
    return f"{prefix}-{digits}-{suffix}"
