from __future__ import annotations

import math
from dataclasses import asdict
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


class ConnectorConfigurationError(ValueError):
    pass


_PERSONALITY_FIELDS = {
    "directness", "brevity", "warmth", "pressure", "humor", "sarcasm",
    "roast", "profanity_level", "profanity_frequency", "initiative",
    "callback", "challenge", "care", "playfulness", "sensitivity",
}
_ALLOWED_ROLES = {
    "group_member", "community_cohost", "organizer",
    "work_assistant", "channel_assistant",
}
_ALLOWED_PRESETS = {"friends", "community", "education", "work", "channel"}
_ALLOWED_AUTHORITY_POLICIES = {"legacy_group", "owner_authoritative", "moderated"}
_ALLOWED_STATUS = {"sandbox", "shadow", "live", "paused"}


def encode_connector_payload(config: ConnectorConfig) -> dict[str, Any]:
    return {
        "identity": asdict(config.identity),
        "behavior": asdict(config.behavior),
        "personality": {"values": dict(config.personality.values)},
        "memory": asdict(config.memory),
        "capabilities": asdict(config.capabilities),
        "authority": {
            "policy": config.authority.policy,
            "owner_subjects": list(config.authority.owner_subjects),
        },
    }


def _dict(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConnectorConfigurationError(f"{name} must be an object")
    return value


def _str(value: Any, name: str, *, allowed: set[str] | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConnectorConfigurationError(f"{name} must be a non-empty string")
    result = value.strip()
    if allowed is not None and result not in allowed:
        raise ConnectorConfigurationError(f"{name} has unsupported value")
    return result


def _bool(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise ConnectorConfigurationError(f"{name} must be boolean")
    return value


def _int(value: Any, name: str, *, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConnectorConfigurationError(f"{name} must be integer")
    if not low <= value <= high:
        raise ConnectorConfigurationError(f"{name} out of range")
    return value


def _float(value: Any, name: str, *, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConnectorConfigurationError(f"{name} must be number")
    result = float(value)
    if not math.isfinite(result) or not low <= result <= high:
        raise ConnectorConfigurationError(f"{name} out of range")
    return result


def decode_connector_payload(
    *,
    connector_id: str,
    connector_type: str,
    status: str,
    version: int,
    payload: dict[str, Any],
) -> ConnectorConfig:
    _str(connector_id, "connector_id")
    connector_type = _str(
        connector_type,
        "connector_type",
        allowed={"telegram_group", "telegram_channel", "telegram_discussion"},
    )
    status = _str(status, "status", allowed=_ALLOWED_STATUS)
    version = _int(version, "version", low=1, high=1_000_000)

    root = _dict(payload, "config")
    identity = _dict(root.get("identity"), "identity")
    behavior = _dict(root.get("behavior"), "behavior")
    personality = _dict(root.get("personality"), "personality")
    memory = _dict(root.get("memory"), "memory")
    capabilities = _dict(root.get("capabilities"), "capabilities")
    authority = _dict(root.get("authority"), "authority")

    role = _str(identity.get("role"), "identity.role", allowed=_ALLOWED_ROLES)
    preset = _str(identity.get("preset"), "identity.preset", allowed=_ALLOWED_PRESETS)

    personality_raw = _dict(personality.get("values"), "personality.values")
    personality_values: dict[str, int] = {}
    for key, value in personality_raw.items():
        if key not in _PERSONALITY_FIELDS:
            raise ConnectorConfigurationError("personality.values contains unsupported field")
        personality_values[key] = _int(
            value,
            f"personality.values.{key}",
            low=0,
            high=10,
        )

    timezone_value = behavior.get("timezone")
    if timezone_value is not None:
        timezone_value = _str(timezone_value, "behavior.timezone")

    soft_limit = _int(
        behavior.get("soft_daily_limit"),
        "behavior.soft_daily_limit",
        low=0,
        high=100,
    )
    hard_limit = _int(
        behavior.get("hard_daily_limit"),
        "behavior.hard_daily_limit",
        low=max(soft_limit, 1),
        high=200,
    )

    memory_scope = _str(
        memory.get("scope_mode"),
        "memory.scope_mode",
        allowed={"connector"},
    )
    cross_connector = _bool(
        memory.get("cross_connector_memory"),
        "memory.cross_connector_memory",
    )
    if cross_connector:
        raise ConnectorConfigurationError(
            "cross_connector_memory is not supported in v1"
        )

    threaded_policy = _str(
        memory.get("threaded_context_policy"),
        "memory.threaded_context_policy",
        allowed={"fail_closed"},
    )

    owner_subjects_raw = authority.get("owner_subjects")
    if not isinstance(owner_subjects_raw, list):
        raise ConnectorConfigurationError("authority.owner_subjects must be an array")
    owner_subjects = tuple(
        _str(item, "authority.owner_subjects[]")
        for item in owner_subjects_raw
    )

    return ConnectorConfig(
        connector_id=connector_id,
        connector_type=connector_type,
        status=status,
        version=version,
        identity=ConnectorIdentityProfile(role=role, preset=preset),
        behavior=ConnectorBehaviorProfile(
            unsolicited_enabled=_bool(
                behavior.get("unsolicited_enabled"),
                "behavior.unsolicited_enabled",
            ),
            initiative=_int(behavior.get("initiative"), "behavior.initiative", low=0, high=10),
            callback_fatigue_minutes=_int(
                behavior.get("callback_fatigue_minutes"),
                "behavior.callback_fatigue_minutes",
                low=0,
                high=1440,
            ),
            cooldown_minutes=_int(
                behavior.get("cooldown_minutes"),
                "behavior.cooldown_minutes",
                low=1,
                high=1440,
            ),
            soft_daily_limit=soft_limit,
            hard_daily_limit=hard_limit,
            mute_minutes=_int(
                behavior.get("mute_minutes"),
                "behavior.mute_minutes",
                low=1,
                high=10080,
            ),
            bot_share_max=_float(
                behavior.get("bot_share_max"),
                "behavior.bot_share_max",
                low=0.01,
                high=1.0,
            ),
            bot_share_window_minutes=_int(
                behavior.get("bot_share_window_minutes"),
                "behavior.bot_share_window_minutes",
                low=5,
                high=1440,
            ),
            bot_share_min_messages=_int(
                behavior.get("bot_share_min_messages"),
                "behavior.bot_share_min_messages",
                low=1,
                high=1000,
            ),
            timezone=timezone_value,
            silence_wakeup_enabled=_bool(
                behavior.get("silence_wakeup_enabled"),
                "behavior.silence_wakeup_enabled",
            ),
            silence_wakeup_after_minutes=_int(
                behavior.get("silence_wakeup_after_minutes"),
                "behavior.silence_wakeup_after_minutes",
                low=60,
                high=2880,
            ),
            silence_wakeup_start_hour=_int(
                behavior.get("silence_wakeup_start_hour"),
                "behavior.silence_wakeup_start_hour",
                low=0,
                high=23,
            ),
            silence_wakeup_end_hour=_int(
                behavior.get("silence_wakeup_end_hour"),
                "behavior.silence_wakeup_end_hour",
                low=1,
                high=24,
            ),
            silence_wakeup_daily_limit=_int(
                behavior.get("silence_wakeup_daily_limit"),
                "behavior.silence_wakeup_daily_limit",
                low=0,
                high=5,
            ),
        ),
        personality=ConnectorPersonalityProfile(values=personality_values),
        memory=ConnectorMemoryProfile(
            scope_mode=memory_scope,
            callbacks_enabled=_bool(
                memory.get("callbacks_enabled"),
                "memory.callbacks_enabled",
            ),
            cross_connector_memory=False,
            threaded_context_policy=threaded_policy,
        ),
        capabilities=ConnectorCapabilityProfile(
            reminders=_bool(capabilities.get("reminders"), "capabilities.reminders"),
            scheduled_actions=_bool(
                capabilities.get("scheduled_actions"),
                "capabilities.scheduled_actions",
            ),
            statement_watch=_bool(
                capabilities.get("statement_watch"),
                "capabilities.statement_watch",
            ),
            silence_wakeup=_bool(
                capabilities.get("silence_wakeup"),
                "capabilities.silence_wakeup",
            ),
        ),
        authority=ConnectorAuthorityProfile(
            policy=_str(
                authority.get("policy"),
                "authority.policy",
                allowed=_ALLOWED_AUTHORITY_POLICIES,
            ),
            owner_subjects=owner_subjects,
        ),
    )
