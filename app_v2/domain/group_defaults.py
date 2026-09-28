from __future__ import annotations

from typing import Any


def new_group_profile() -> dict[str, Any]:
    """Return a fresh copy of the accepted low-initiative Friends starting profile.

    Only initialize a new (or explicitly reconciled empty) group. Never reset
    a populated group profile during ordinary Telegram ingest.
    """
    return {
        "profile": "friends",
        "unsolicited_enabled": True,
        "initiative": 3,
        "humor": 8,
        "sarcasm": 8,
        "roast": 7,
        "callback": 8,
        "profanity_level": 6,
        "profanity_frequency": 3,
        "sensitivity": 8,
        "callback_fatigue_minutes": 180,
        "timezone": "Europe/Moscow",
        "silence_wakeup_enabled": True,
        "silence_wakeup_after_minutes": 180,
        "silence_wakeup_start_hour": 10,
        "silence_wakeup_end_hour": 22,
        "silence_wakeup_daily_limit": 1,
    }
