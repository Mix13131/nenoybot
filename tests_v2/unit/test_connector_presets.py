from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from app_v2.repositories.connector_repo import PersistedConnectorRecord
from app_v2.services.connector_codec import (
    decode_connector_payload,
    encode_connector_payload,
)
from app_v2.services.connector_onboarding import (
    ConnectorOnboardingError,
    ConnectorOnboardingService,
)
from app_v2.services.connector_presets import (
    ConnectorPresetError,
    available_connector_presets,
    build_anna_diamond_voice_connector,
    build_connector_preset,
)


def test_education_community_preset_is_pinned_and_conservative() -> None:
    config = build_connector_preset(
        "education_community_v1",
        connector_id="conn_anna_test",
    )

    assert config.identity.role == "community_cohost"
    assert config.identity.preset == "education"
    assert config.status == "live"
    assert config.version == 1

    assert config.behavior.unsolicited_enabled is True
    assert config.behavior.initiative == 4
    assert config.behavior.cooldown_minutes == 30
    assert config.behavior.soft_daily_limit == 3
    assert config.behavior.hard_daily_limit == 5
    assert config.behavior.bot_share_max == 0.08
    assert config.behavior.timezone == "Europe/Moscow"
    assert config.behavior.silence_wakeup_enabled is False
    assert config.behavior.silence_wakeup_daily_limit == 0

    personality = config.personality.values
    assert personality["warmth"] == 9
    assert personality["sensitivity"] == 9
    assert personality["care"] == 9
    assert personality["humor"] == 5
    assert personality["sarcasm"] == 2
    assert personality["roast"] == 0
    assert personality["profanity_level"] == 0
    assert personality["profanity_frequency"] == 0

    assert config.memory.scope_mode == "connector"
    assert config.memory.callbacks_enabled is True
    assert config.memory.cross_connector_memory is False
    assert config.memory.threaded_context_policy == "fail_closed"

    assert config.capabilities.reminders is True
    assert config.capabilities.scheduled_actions is True
    assert config.capabilities.silence_wakeup is False
    assert config.authority.policy == "owner_authoritative"
    assert config.authority.owner_subjects == ()


def test_anna_connector_is_instance_of_generic_education_preset() -> None:
    generic = build_connector_preset(
        "education_community_v1",
        connector_id="conn_same",
    )
    anna = build_anna_diamond_voice_connector(
        connector_id="conn_same",
    )

    assert anna == generic


def test_education_preset_round_trips_through_strict_connector_codec() -> None:
    config = build_anna_diamond_voice_connector(
        connector_id="conn_codec",
        owner_subjects=("user:anna",),
    )
    payload = encode_connector_payload(config)

    decoded = decode_connector_payload(
        connector_id=config.connector_id,
        connector_type=config.connector_type,
        status=config.status,
        version=config.version,
        payload=payload,
    )

    assert decoded == config
    assert decoded.authority.owner_subjects == ("user:anna",)
    assert decoded.memory.cross_connector_memory is False


def test_unknown_connector_preset_fails_closed() -> None:
    assert available_connector_presets() == ("education_community_v1",)
    with pytest.raises(ConnectorPresetError, match="unknown"):
        build_connector_preset(
            "unknown",
            connector_id="conn_x",
        )


class FakeGroupRepo:
    def __init__(self, group):
        self.group = group
        self.whitelist_calls = []

    def find_groups_by_exact_title(self, title):
        if self.group is None:
            return []
        if isinstance(self.group, list):
            return self.group
        return [self.group]

    def set_whitelisted(self, scope_id, enabled):
        self.whitelist_calls.append((scope_id, enabled))
        return True


class FakeConnectorRepo:
    def __init__(self, record=None, *, create_result=True):
        self.record = record
        self.create_result = create_result
        self.create_calls = []

    def get_for_scope(self, scope_type, scope_id):
        return self.record

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return self.create_result


def group(*, title="Anna Lab", whitelisted=False, active=True):
    return SimpleNamespace(
        title=title,
        telegram_chat_id="-42",
        is_whitelisted=whitelisted,
        is_active=active,
        profile={"legacy_sentinel": "keep"},
    )


def test_onboarding_creates_connector_then_whitelists_without_profile_rewrite() -> None:
    group_repo = FakeGroupRepo(group())
    connector_repo = FakeConnectorRepo()
    service = ConnectorOnboardingService(
        group_repo=group_repo,
        connector_repo=connector_repo,
    )

    result = service.apply_exact_title(
        title="Anna Lab",
        preset_name="education_community_v1",
    )

    assert result.created is True
    assert result.whitelisted is True
    assert group_repo.whitelist_calls == [("-42", True)]
    assert len(connector_repo.create_calls) == 1
    config = connector_repo.create_calls[0]["config"]
    assert config.identity.role == "community_cohost"


def test_onboarding_rejects_existing_different_connector_without_whitelisting() -> None:
    desired = build_connector_preset(
        "education_community_v1",
        connector_id="conn_fake",
    )
    different = replace(
        desired,
        identity=replace(desired.identity, role="organizer"),
    )
    record = PersistedConnectorRecord(
        connector_id=different.connector_id,
        scope_type="group",
        scope_id="-42",
        connector_type=different.connector_type,
        status=different.status,
        version=different.version,
        config=encode_connector_payload(different),
    )
    group_repo = FakeGroupRepo(group())
    connector_repo = FakeConnectorRepo(record)
    service = ConnectorOnboardingService(
        group_repo=group_repo,
        connector_repo=connector_repo,
    )

    with pytest.raises(ConnectorOnboardingError, match="differs"):
        service.apply_exact_title(
            title="Anna Lab",
            preset_name="education_community_v1",
        )

    assert group_repo.whitelist_calls == []
    assert connector_repo.create_calls == []


def test_onboarding_fails_closed_on_missing_ambiguous_or_inactive_group() -> None:
    connector_repo = FakeConnectorRepo()

    with pytest.raises(ConnectorOnboardingError, match="not found"):
        ConnectorOnboardingService(
            group_repo=FakeGroupRepo(None),
            connector_repo=connector_repo,
        ).apply_exact_title(
            title="Anna Lab",
            preset_name="education_community_v1",
        )

    duplicate = [group(), group()]
    with pytest.raises(ConnectorOnboardingError, match="ambiguous"):
        ConnectorOnboardingService(
            group_repo=FakeGroupRepo(duplicate),
            connector_repo=connector_repo,
        ).apply_exact_title(
            title="Anna Lab",
            preset_name="education_community_v1",
        )

    with pytest.raises(ConnectorOnboardingError, match="not active"):
        ConnectorOnboardingService(
            group_repo=FakeGroupRepo(group(active=False)),
            connector_repo=connector_repo,
        ).apply_exact_title(
            title="Anna Lab",
            preset_name="education_community_v1",
        )



def test_preset_builds_do_not_share_mutable_personality_mapping() -> None:
    first = build_connector_preset(
        "education_community_v1",
        connector_id="conn_first",
    )
    second = build_connector_preset(
        "education_community_v1",
        connector_id="conn_second",
    )

    assert first.personality.values is not second.personality.values
    first.personality.values["warmth"] = 1
    assert second.personality.values["warmth"] == 9

    third = build_connector_preset(
        "education_community_v1",
        connector_id="conn_third",
    )
    assert third.personality.values["warmth"] == 9
