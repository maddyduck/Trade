"""Tests for app.services.references."""
from app.services.references import _trade_prefix, generate_reference


def test_prefix_drops_vowels_preferentially():
    # Mark McKinney → "McKinneyPlumbing" → prefer consonants
    assert _trade_prefix("mckinney-plumbing") == "MCK"


def test_prefix_short_slugs():
    assert _trade_prefix("ab") == "ABX"
    assert _trade_prefix("a") == "AXX"


def test_reference_format():
    ref = generate_reference("mckinney-plumbing")
    parts = ref.split("-")
    assert len(parts) == 3
    assert parts[0] == "MCK"
    assert len(parts[1]) == 4 and parts[1].isdigit()
    assert len(parts[2]) == 2


def test_references_are_distinct():
    refs = {generate_reference("mckinney-plumbing") for _ in range(200)}
    # Vanishingly small chance of collision in 200 tries — if this fails
    # look at SUFFIX_ALPHABET size.
    assert len(refs) >= 195
