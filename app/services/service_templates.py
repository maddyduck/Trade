"""Pre-baked service templates for new trades.

Speeds up onboarding — instead of staring at a blank form, a new plumber
sees "Boiler service · 60 min · £45 deposit" and one tap creates it.
Trade can edit before saving or after.
"""
from __future__ import annotations


# Each entry: (key, name, description, icon, duration, deposit_pence, is_emergency)
PLUMBING_TEMPLATES = [
    {
        "key": "emergency_leak",
        "name": "Emergency leak repair",
        "description": "Burst pipes, urgent leaks, water shutoff needed.",
        "icon": "🚨",
        "duration_minutes": 90,
        "deposit_pence": 6000,
        "is_emergency": True,
    },
    {
        "key": "boiler_service",
        "name": "Annual boiler service",
        "description": "Manufacturer-recommended yearly check. Includes safety inspection.",
        "icon": "🔥",
        "duration_minutes": 60,
        "deposit_pence": 4500,
        "is_emergency": False,
    },
    {
        "key": "boiler_callout",
        "name": "Boiler not working",
        "description": "No heat, no hot water, error codes, weird noises.",
        "icon": "❄️",
        "duration_minutes": 60,
        "deposit_pence": 4500,
        "is_emergency": False,
    },
    {
        "key": "general_callout",
        "name": "General plumbing callout",
        "description": "Blocked drains, dripping taps, fixture swaps, small repairs.",
        "icon": "🔧",
        "duration_minutes": 60,
        "deposit_pence": 3000,
        "is_emergency": False,
    },
    {
        "key": "bathroom_consult",
        "name": "Bathroom consultation",
        "description": "On-site quote for a new bathroom or shower install.",
        "icon": "🛁",
        "duration_minutes": 45,
        "deposit_pence": 3000,
        "is_emergency": False,
    },
    {
        "key": "power_flush",
        "name": "Power flush",
        "description": "Full system flush to clear sludge and improve heating performance.",
        "icon": "💧",
        "duration_minutes": 240,
        "deposit_pence": 8000,
        "is_emergency": False,
    },
    {
        "key": "radiator_install",
        "name": "Radiator install / swap",
        "description": "Replace existing or fit a new radiator.",
        "icon": "♨️",
        "duration_minutes": 120,
        "deposit_pence": 5000,
        "is_emergency": False,
    },
]

ELECTRICIAN_TEMPLATES = [
    {
        "key": "emergency_electrical",
        "name": "Emergency callout",
        "description": "No power, sparks, burning smell, urgent fault.",
        "icon": "⚡",
        "duration_minutes": 90,
        "deposit_pence": 6000,
        "is_emergency": True,
    },
    {
        "key": "fuse_board",
        "name": "Consumer unit / fuse board",
        "description": "Tripping breakers, RCD issues, fuseboard upgrade.",
        "icon": "🔌",
        "duration_minutes": 90,
        "deposit_pence": 5000,
        "is_emergency": False,
    },
    {
        "key": "lighting",
        "name": "Lighting install",
        "description": "Downlights, pendants, outdoor lights, dimmer switches.",
        "icon": "💡",
        "duration_minutes": 60,
        "deposit_pence": 3500,
        "is_emergency": False,
    },
    {
        "key": "ev_charger",
        "name": "EV charger install",
        "description": "7kW home charger, full install with certification.",
        "icon": "🔋",
        "duration_minutes": 240,
        "deposit_pence": 10000,
        "is_emergency": False,
    },
    {
        "key": "eicr",
        "name": "EICR inspection",
        "description": "Electrical Installation Condition Report for landlords/sale.",
        "icon": "📋",
        "duration_minutes": 120,
        "deposit_pence": 5000,
        "is_emergency": False,
    },
]


TEMPLATES_BY_TRADE = {
    "plumber": PLUMBING_TEMPLATES,
    "heating_engineer": PLUMBING_TEMPLATES,
    "electrician": ELECTRICIAN_TEMPLATES,
}


def templates_for(trade_type: str | None) -> list[dict]:
    """Return the right shortlist for a given trade type."""
    if not trade_type:
        return PLUMBING_TEMPLATES  # safe default for V1 NI focus
    return TEMPLATES_BY_TRADE.get(trade_type, PLUMBING_TEMPLATES)
