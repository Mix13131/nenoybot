from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from app_v2.domain.connectors import (
    ConnectorAuthorityProfile,
    ConnectorBehaviorProfile,
    ConnectorCapabilityProfile,
    ConnectorConfig,
    ConnectorIdentityProfile,
    ConnectorMemoryProfile,
    ConnectorPersonalityProfile,
)


class ConnectorPresetError(ValueError):
    pass


@dataclass(frozen=True)
class ConnectorPresetDefinition:
    name: str
    identity: ConnectorIdentityProfile
    behavior: ConnectorBehaviorProfile
    personality: ConnectorPersonalityProfile
    memory: ConnectorMemoryProfile
    capabilities: ConnectorCapabilityProfile
    authority_policy: str

    def build(
        self,
        *,
        connector_id: str,
        version: int = 1,
        status: str = "live",
        owner_subjects: tuple[str, ...] = (),
    ) -> ConnectorConfig:
        if not str(connector_id or "").strip():
            raise ConnectorPresetError("connector_id is required")
        if version < 1:
            raise ConnectorPresetError("version must be >= 1")
        return ConnectorConfig(
            connector_id=str(connector_id).strip(),
            connector_type="telegram_group",
            status=status,
            version=version,
            identity=self.identity,
            behavior=self.behavior,
            personality=ConnectorPersonalityProfile(
                values=dict(self.personality.values),
            ),
            memory=self.memory,
            capabilities=self.capabilities,
            authority=ConnectorAuthorityProfile(
                policy=self.authority_policy,
                owner_subjects=tuple(owner_subjects),
            ),
        )


_EDUCATION_COMMUNITY_V1 = ConnectorPresetDefinition(
    name="education_community_v1",
    identity=ConnectorIdentityProfile(
        role="community_cohost",
        preset="education",
    ),
    behavior=ConnectorBehaviorProfile(
        unsolicited_enabled=True,
        initiative=4,
        callback_fatigue_minutes=360,
        cooldown_minutes=30,
        soft_daily_limit=3,
        hard_daily_limit=5,
        mute_minutes=180,
        bot_share_max=0.08,
        bot_share_window_minutes=120,
        bot_share_min_messages=12,
        timezone="Europe/Moscow",
        silence_wakeup_enabled=False,
        silence_wakeup_after_minutes=720,
        silence_wakeup_start_hour=10,
        silence_wakeup_end_hour=22,
        silence_wakeup_daily_limit=0,
    ),
    personality=ConnectorPersonalityProfile(
        values={
            "directness": 6,
            "brevity": 7,
            "warmth": 9,
            "pressure": 2,
            "humor": 5,
            "sarcasm": 2,
            "roast": 0,
            "profanity_level": 0,
            "profanity_frequency": 0,
            "initiative": 4,
            "callback": 7,
            "challenge": 3,
            "care": 9,
            "playfulness": 5,
            "sensitivity": 9,
        }
    ),
    memory=ConnectorMemoryProfile(
        scope_mode="connector",
        callbacks_enabled=True,
        cross_connector_memory=False,
        threaded_context_policy="fail_closed",
    ),
    capabilities=ConnectorCapabilityProfile(
        reminders=True,
        scheduled_actions=True,
        statement_watch=True,
        silence_wakeup=False,
    ),
    authority_policy="owner_authoritative",
)


_PRESETS: Mapping[str, ConnectorPresetDefinition] = {
    _EDUCATION_COMMUNITY_V1.name: _EDUCATION_COMMUNITY_V1,
}


def available_connector_presets() -> tuple[str, ...]:
    return tuple(sorted(_PRESETS))


def build_connector_preset(
    preset_name: str,
    *,
    connector_id: str,
    version: int = 1,
    status: str = "live",
    owner_subjects: tuple[str, ...] = (),
) -> ConnectorConfig:
    normalized = str(preset_name or "").strip()
    preset = _PRESETS.get(normalized)
    if preset is None:
        raise ConnectorPresetError("unknown connector preset")
    return preset.build(
        connector_id=connector_id,
        version=version,
        status=status,
        owner_subjects=owner_subjects,
    )


def build_anna_diamond_voice_connector(
    *,
    connector_id: str,
    version: int = 1,
    status: str = "live",
    owner_subjects: tuple[str, ...] = (),
) -> ConnectorConfig:
    """First concrete community connector instance on the generic education preset."""
    return build_connector_preset(
        "education_community_v1",
        connector_id=connector_id,
        version=version,
        status=status,
        owner_subjects=owner_subjects,
    )
