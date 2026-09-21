from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app_v2.repositories.connector_repo import PersistedConnectorRecord
from app_v2.repositories.group_context_repo import GroupContext, ParticipantContext
from app_v2.services.connector_codec import (
    ConnectorConfigurationError,
    decode_connector_payload,
    encode_connector_payload,
)
from app_v2.services.connector_resolver import (
    LegacyGroupConnectorResolver,
    PersistedGroupConnectorResolver,
    migrated_legacy_connector,
)


def context(profile=None) -> GroupContext:
    base = {
        "profile": "friends",
        "unsolicited_enabled": True,
        "initiative": 3,
        "humor": 8,
        "sarcasm": 8,
        "roast": 7,
        "callback": 8,
        "profanity_level": 5,
        "profanity_frequency": 3,
        "sensitivity": 8,
        "timezone": "Europe/Moscow",
        "silence_wakeup_enabled": True,
        "silence_wakeup_after_minutes": 180,
        "silence_wakeup_start_hour": 10,
        "silence_wakeup_end_hour": 22,
        "silence_wakeup_daily_limit": 1,
    }
    base.update(profile or {})
    return GroupContext(
        internal_chat_id=1,
        telegram_chat_id="-100777",
        title="Friends",
        is_whitelisted=True,
        is_active=True,
        silent_until=None,
        profile=base,
        participant=ParticipantContext(telegram_user_id=None),
    )


def test_connector_codec_round_trips_migrated_legacy_config() -> None:
    config = migrated_legacy_connector(context())
    payload = encode_connector_payload(config)

    decoded = decode_connector_payload(
        connector_id=config.connector_id,
        connector_type=config.connector_type,
        status=config.status,
        version=config.version,
        payload=payload,
    )

    assert decoded == config
    assert decoded.memory.cross_connector_memory is False
    assert decoded.behavior.timezone == "Europe/Moscow"


def test_connector_codec_rejects_cross_connector_memory() -> None:
    config = migrated_legacy_connector(context())
    payload = encode_connector_payload(config)
    payload["memory"]["cross_connector_memory"] = True

    with pytest.raises(
        ConnectorConfigurationError,
        match="cross_connector_memory",
    ):
        decode_connector_payload(
            connector_id=config.connector_id,
            connector_type=config.connector_type,
            status=config.status,
            version=config.version,
            payload=payload,
        )


def test_connector_codec_rejects_non_finite_persisted_limits() -> None:
    config = migrated_legacy_connector(context())
    payload = encode_connector_payload(config)
    payload["behavior"]["bot_share_max"] = float("nan")

    with pytest.raises(ConnectorConfigurationError):
        decode_connector_payload(
            connector_id=config.connector_id,
            connector_type=config.connector_type,
            status=config.status,
            version=config.version,
            payload=payload,
        )


class Repo:
    def __init__(self, record=None):
        self.record = record
        self.calls = []

    def get_for_scope(self, scope_type, scope_id):
        self.calls.append((scope_type, scope_id))
        return self.record


def test_persisted_resolver_falls_back_only_when_record_absent() -> None:
    ctx = context()
    resolver = PersistedGroupConnectorResolver(Repo())

    result = resolver.resolve(ctx)

    assert result.identity.preset == "friends"
    assert result.behavior.initiative == 3
    assert result.connector_id.startswith("legacy:")


def test_persisted_resolver_prefers_valid_registry_record() -> None:
    ctx = context()
    persisted = migrated_legacy_connector(ctx)
    persisted = replace(
        persisted,
        version=2,
        identity=replace(persisted.identity, role="community_cohost"),
    )
    record = PersistedConnectorRecord(
        connector_id=persisted.connector_id,
        scope_type="group",
        scope_id=ctx.telegram_chat_id,
        connector_type=persisted.connector_type,
        status=persisted.status,
        version=persisted.version,
        config=encode_connector_payload(persisted),
    )

    result = PersistedGroupConnectorResolver(Repo(record)).resolve(ctx)

    assert result.version == 2
    assert result.identity.role == "community_cohost"


def test_malformed_persisted_connector_does_not_fallback_to_legacy() -> None:
    ctx = context()
    persisted = migrated_legacy_connector(ctx)
    payload = encode_connector_payload(persisted)
    payload["memory"]["cross_connector_memory"] = True
    record = PersistedConnectorRecord(
        connector_id=persisted.connector_id,
        scope_type="group",
        scope_id=ctx.telegram_chat_id,
        connector_type=persisted.connector_type,
        status=persisted.status,
        version=1,
        config=payload,
    )

    with pytest.raises(ConnectorConfigurationError):
        PersistedGroupConnectorResolver(Repo(record)).resolve(ctx)


def test_persisted_scope_binding_cannot_be_overridden_by_json() -> None:
    ctx = context()
    persisted = migrated_legacy_connector(ctx)
    payload = encode_connector_payload(persisted)
    payload["scope_id"] = "another-group"
    record = PersistedConnectorRecord(
        connector_id=persisted.connector_id,
        scope_type="group",
        scope_id=ctx.telegram_chat_id,
        connector_type=persisted.connector_type,
        status=persisted.status,
        version=1,
        config=payload,
    )

    result = PersistedGroupConnectorResolver(Repo(record)).resolve(ctx)

    assert result.connector_id == persisted.connector_id
    assert not hasattr(result, "scope_id")



def test_registry_scope_binding_type_mismatch_is_rejected_before_storage() -> None:
    from app_v2.repositories.connector_repo import ConnectorRepository

    config = migrated_legacy_connector(context())
    channel_config = replace(config, connector_type="telegram_channel")

    class NoDB:
        def transaction(self):
            raise AssertionError("database must not be touched")

    repo = ConnectorRepository(NoDB())

    with pytest.raises(ValueError, match="group scope requires"):
        repo.create(
            scope_type="group",
            scope_id=context().telegram_chat_id,
            config=channel_config,
        )
