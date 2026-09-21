from __future__ import annotations

from typing import Any

from app_v2.domain.connectors import (
    ConnectorAuthorityProfile,
    ConnectorBehaviorProfile,
    ConnectorCapabilityProfile,
    ConnectorConfig,
    ConnectorIdentityProfile,
    ConnectorMemoryProfile,
    ConnectorPersonalityProfile,
)
from app_v2.repositories.group_context_repo import GroupContext


_PERSONALITY_FIELDS = (
    "directness",
    "brevity",
    "warmth",
    "pressure",
    "humor",
    "sarcasm",
    "roast",
    "profanity_level",
    "profanity_frequency",
    "initiative",
    "callback",
    "challenge",
    "care",
    "playfulness",
    "sensitivity",
)

_ALLOWED_ROLES = {
    "group_member",
    "community_cohost",
    "organizer",
    "work_assistant",
    "channel_assistant",
}


def _int(value: Any, default: int, *, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, parsed))


def _float(value: Any, default: float, *, low: float, high: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, parsed))


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        return default
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    return default


def _text(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text or default


class LegacyGroupConnectorResolver:
    """Adapt legacy chats.group_profile into structured ConnectorConfig.

    TASK 34 deliberately keeps the legacy JSONB profile as storage. This
    adapter gives the Brain one stable connector contract while preserving
    current live-group behavior.
    """

    def resolve(self, group_context: GroupContext) -> ConnectorConfig:
        profile = dict(group_context.profile or {})
        preset = _text(profile.get("profile"), "friends")
        role = _text(profile.get("connector_role"), "group_member")
        if role not in _ALLOWED_ROLES:
            role = "group_member"

        personality_values: dict[str, int] = {}
        for field in _PERSONALITY_FIELDS:
            if field in profile:
                personality_values[field] = _int(
                    profile.get(field),
                    0,
                    low=0,
                    high=10,
                )

        unsolicited_enabled = _bool(
            profile.get("unsolicited_enabled"),
            False,
        )
        silence_wakeup_enabled = _bool(
            profile.get("silence_wakeup_enabled"),
            False,
        )
        callback_level = _int(profile.get("callback"), 10, low=0, high=10)

        status = (
            "live"
            if group_context.is_whitelisted and group_context.is_active
            else "paused"
        )

        return ConnectorConfig(
            connector_id=f"legacy:telegram_group:{group_context.telegram_chat_id}",
            connector_type="telegram_group",
            status=status,
            version=1,
            identity=ConnectorIdentityProfile(
                role=role,
                preset=preset,
            ),
            behavior=ConnectorBehaviorProfile(
                unsolicited_enabled=unsolicited_enabled,
                initiative=_int(profile.get("initiative"), 6, low=0, high=10),
                callback_fatigue_minutes=_int(
                    profile.get("callback_fatigue_minutes"),
                    180,
                    low=0,
                    high=1440,
                ),
                cooldown_minutes=_int(
                    profile.get("cooldown_minutes"),
                    12,
                    low=1,
                    high=1440,
                ),
                soft_daily_limit=_int(
                    profile.get("soft_daily_limit"),
                    6,
                    low=0,
                    high=100,
                ),
                hard_daily_limit=_int(
                    profile.get("hard_daily_limit"),
                    10,
                    low=1,
                    high=200,
                ),
                mute_minutes=_int(
                    profile.get("mute_minutes"),
                    120,
                    low=1,
                    high=10080,
                ),
                bot_share_max=_float(
                    profile.get("bot_share_max"),
                    0.10,
                    low=0.01,
                    high=1.0,
                ),
                bot_share_window_minutes=_int(
                    profile.get("bot_share_window_minutes"),
                    60,
                    low=5,
                    high=1440,
                ),
                bot_share_min_messages=_int(
                    profile.get("bot_share_min_messages"),
                    10,
                    low=1,
                    high=1000,
                ),
                timezone=(
                    str(profile.get("timezone")).strip()
                    if str(profile.get("timezone") or "").strip()
                    else None
                ),
                silence_wakeup_enabled=silence_wakeup_enabled,
                silence_wakeup_after_minutes=_int(
                    profile.get("silence_wakeup_after_minutes"),
                    180,
                    low=60,
                    high=2880,
                ),
                silence_wakeup_start_hour=_int(
                    profile.get("silence_wakeup_start_hour"),
                    10,
                    low=0,
                    high=23,
                ),
                silence_wakeup_end_hour=_int(
                    profile.get("silence_wakeup_end_hour"),
                    22,
                    low=1,
                    high=24,
                ),
                silence_wakeup_daily_limit=_int(
                    profile.get("silence_wakeup_daily_limit"),
                    1,
                    low=0,
                    high=5,
                ),
            ),
            personality=ConnectorPersonalityProfile(
                values=personality_values,
            ),
            memory=ConnectorMemoryProfile(
                scope_mode="connector",
                callbacks_enabled=callback_level > 0,
                cross_connector_memory=False,
                threaded_context_policy="fail_closed",
            ),
            capabilities=ConnectorCapabilityProfile(
                reminders=True,
                scheduled_actions=True,
                statement_watch=True,
                silence_wakeup=silence_wakeup_enabled,
            ),
            authority=ConnectorAuthorityProfile(
                policy=_text(
                    profile.get("authority_policy"),
                    "legacy_group",
                ),
            ),
        )
