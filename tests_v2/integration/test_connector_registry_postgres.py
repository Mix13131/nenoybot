from __future__ import annotations

import os
from dataclasses import replace

import pytest

from app_v2.db.migrations import run_migrations
from app_v2.domain.connectors import ConnectorBehaviorProfile
from app_v2.repositories.connector_repo import ConnectorRepository
from app_v2.repositories.group_context_repo import GroupContext, ParticipantContext
from app_v2.services.connector_codec import decode_connector_payload
from app_v2.services.connector_resolver import migrated_legacy_connector


def _database_url() -> str | None:
    return os.getenv("NENOY_V2_TEST_DATABASE_URL")


def _context(scope_id: str) -> GroupContext:
    return GroupContext(
        internal_chat_id=1,
        telegram_chat_id=scope_id,
        title="Connector Registry Test",
        is_whitelisted=True,
        is_active=True,
        silent_until=None,
        profile={
            "profile": "friends",
            "unsolicited_enabled": True,
            "initiative": 3,
            "humor": 8,
            "sarcasm": 8,
            "roast": 7,
            "callback": 8,
            "timezone": "Europe/Moscow",
            "silence_wakeup_enabled": True,
        },
        participant=ParticipantContext(telegram_user_id=None),
    )


def test_postgres_connector_registry_versions_are_durable_and_immutable():
    database_url = _database_url()
    if not database_url:
        pytest.skip("NENOY_V2_TEST_DATABASE_URL is not configured")

    psycopg = pytest.importorskip("psycopg")
    run_migrations(database_url)
    scope_id = "-100910000035"

    with psycopg.connect(database_url) as conn:
        repo = ConnectorRepository(conn)
        repo.delete_for_scope("group", scope_id)

        v1 = migrated_legacy_connector(_context(scope_id))
        assert repo.create(
            scope_type="group",
            scope_id=scope_id,
            config=v1,
            created_by="test",
        ) is True
        assert repo.create(
            scope_type="group",
            scope_id=scope_id,
            config=v1,
            created_by="test",
        ) is False

        record1 = repo.get_for_scope("group", scope_id)
        assert record1 is not None
        assert record1.version == 1
        decoded1 = decode_connector_payload(
            connector_id=record1.connector_id,
            connector_type=record1.connector_type,
            status=record1.status,
            version=record1.version,
            payload=record1.config,
        )
        assert decoded1.behavior.initiative == 3

        v2 = replace(
            v1,
            version=2,
            behavior=replace(v1.behavior, initiative=6),
        )
        assert repo.append_version(
            connector_id=v1.connector_id,
            expected_version=1,
            config=v2,
            created_by="test",
        ) == 2

        record2 = repo.get_for_scope("group", scope_id)
        assert record2 is not None
        assert record2.version == 2
        decoded2 = decode_connector_payload(
            connector_id=record2.connector_id,
            connector_type=record2.connector_type,
            status=record2.status,
            version=record2.version,
            payload=record2.config,
        )
        assert decoded2.behavior.initiative == 6

        rows = conn.execute(
            """
            SELECT version, config
            FROM connector_versions
            WHERE connector_id=%s
            ORDER BY version
            """,
            (v1.connector_id,),
        ).fetchall()
        assert [row[0] for row in rows] == [1, 2]
        assert rows[0][1]["behavior"]["initiative"] == 3
        assert rows[1][1]["behavior"]["initiative"] == 6

        with pytest.raises(RuntimeError, match="version conflict"):
            repo.append_version(
                connector_id=v1.connector_id,
                expected_version=1,
                config=replace(v2, version=2),
                created_by="stale-test",
            )

        assert repo.delete_for_scope("group", scope_id) is True
        assert repo.get_for_scope("group", scope_id) is None
        remaining = conn.execute(
            "SELECT COUNT(*) FROM connector_versions WHERE connector_id=%s",
            (v1.connector_id,),
        ).fetchone()
        assert remaining[0] == 0
