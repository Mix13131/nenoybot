from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


_PERSONALITY_FIELDS = {
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
}


@dataclass(frozen=True)
class ConnectorIdentityProfile:
    role: str = "group_member"
    preset: str = "friends"


@dataclass(frozen=True)
class ConnectorBehaviorProfile:
    unsolicited_enabled: bool = False
    initiative: int = 6
    callback_fatigue_minutes: int = 180
    cooldown_minutes: int = 12
    soft_daily_limit: int = 6
    hard_daily_limit: int = 10
    mute_minutes: int = 120
    bot_share_max: float = 0.10
    bot_share_window_minutes: int = 60
    bot_share_min_messages: int = 10
    timezone: str | None = None
    silence_wakeup_enabled: bool = False
    silence_wakeup_after_minutes: int = 180
    silence_wakeup_start_hour: int = 10
    silence_wakeup_end_hour: int = 22
    silence_wakeup_daily_limit: int = 1


@dataclass(frozen=True)
class ConnectorPersonalityProfile:
    values: Mapping[str, int] = field(default_factory=dict)

    def as_context_profile(self, preset: str) -> dict[str, Any]:
        # Keep the legacy profile name in the deterministic personality context
        # for observability while only exposing known personality dimensions.
        result: dict[str, Any] = {"profile": preset}
        result.update(
            {
                key: int(value)
                for key, value in self.values.items()
                if key in _PERSONALITY_FIELDS
            }
        )
        return result


@dataclass(frozen=True)
class ConnectorMemoryProfile:
    scope_mode: str = "connector"
    callbacks_enabled: bool = True
    cross_connector_memory: bool = False
    threaded_context_policy: str = "fail_closed"


@dataclass(frozen=True)
class ConnectorCapabilityProfile:
    reminders: bool = True
    scheduled_actions: bool = True
    statement_watch: bool = True
    silence_wakeup: bool = False

    def enabled_names(self) -> tuple[str, ...]:
        result = []
        for name in (
            "reminders",
            "scheduled_actions",
            "statement_watch",
            "silence_wakeup",
        ):
            if bool(getattr(self, name)):
                result.append(name)
        return tuple(result)


@dataclass(frozen=True)
class ConnectorAuthorityProfile:
    policy: str = "legacy_group"
    owner_subjects: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConnectorConfig:
    connector_id: str
    connector_type: str
    status: str
    version: int
    identity: ConnectorIdentityProfile
    behavior: ConnectorBehaviorProfile
    personality: ConnectorPersonalityProfile
    memory: ConnectorMemoryProfile
    capabilities: ConnectorCapabilityProfile
    authority: ConnectorAuthorityProfile

    def public_state(self) -> dict[str, Any]:
        """Sanitized connector metadata safe for generation/action context."""
        return {
            "connector_type": self.connector_type,
            "status": self.status,
            "version": self.version,
            "role": self.identity.role,
            "preset": self.identity.preset,
            "memory": {
                "scope_mode": self.memory.scope_mode,
                "cross_connector_memory": self.memory.cross_connector_memory,
                "threaded_context_policy": self.memory.threaded_context_policy,
            },
            "capabilities": list(self.capabilities.enabled_names()),
            "authority_policy": self.authority.policy,
        }
