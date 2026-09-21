from __future__ import annotations

import os

import pytest

from app_v2.db.migrations import run_migrations
from app_v2.repositories.connector_repo import ConnectorRepository
from app_v2.repositories.group_context_repo import GroupContextRepository
from app_v2.services.connector_codec import decode_connector_payload
from app_v2.services.connector_onboarding import ConnectorOnboardingService


def _database_url() -> str | None:
    return os.getenv("NENOY_V2_TEST_DATABASE_URL")


def test_postgres_preset_onboarding_preserves_legacy_profile_and_is_idempotent():
    database_url = _database_url()
    if not database_url:
        pytest.skip("NENOY_V2_TEST_DATABASE_URL is not configured")

    psycopg = pytest.importorskip("psycopg")
    run_migrations(database_url)

    chat_id = -100910000038
    title = "Anna Connector Sandbox Integration"

    with psycopg.connect(database_url) as conn:
        connector_repo = ConnectorRepository(conn)
        connector_repo.delete_for_scope("group", str(chat_id))
        conn.execute("DELETE FROM chats WHERE telegram_chat_id=%s", (chat_id,))
        conn.execute(
            """
            INSERT INTO chats(
                telegram_chat_id, chat_type, title, group_profile,
                is_whitelisted, is_active
            )
            VALUES (
                %s, 'group', %s,
                '{"legacy_sentinel":"keep","custom_value":17}'::jsonb,
                FALSE, TRUE
            )
            """,
            (chat_id, title),
        )
        conn.commit()

        group_repo = GroupContextRepository(conn)
        service = ConnectorOnboardingService(
            group_repo=group_repo,
            connector_repo=connector_repo,
        )

        first = service.apply_exact_title(
            title=title,
            preset_name="education_community_v1",
            created_by="integration-test",
        )
        assert first.created is True
        assert first.whitelisted is True
        assert first.version == 1

        chat = conn.execute(
            """
            SELECT is_whitelisted, is_active, group_profile
            FROM chats
            WHERE telegram_chat_id=%s
            """,
            (chat_id,),
        ).fetchone()
        assert chat[0] is True
        assert chat[1] is True
        assert chat[2] == {
            "legacy_sentinel": "keep",
            "custom_value": 17,
        }

        record = connector_repo.get_for_scope("group", str(chat_id))
        assert record is not None
        decoded = decode_connector_payload(
            connector_id=record.connector_id,
            connector_type=record.connector_type,
            status=record.status,
            version=record.version,
            payload=record.config,
        )
        assert decoded.identity.role == "community_cohost"
        assert decoded.identity.preset == "education"
        assert decoded.behavior.initiative == 4
        assert decoded.behavior.silence_wakeup_enabled is False
        assert decoded.personality.values["warmth"] == 9
        assert decoded.personality.values["roast"] == 0
        assert decoded.memory.cross_connector_memory is False
        assert decoded.authority.policy == "owner_authoritative"

        second = service.apply_exact_title(
            title=title,
            preset_name="education_community_v1",
            created_by="integration-test-repeat",
        )
        assert second.created is False
        assert second.whitelisted is True

        versions = conn.execute(
            """
            SELECT COUNT(*)
            FROM connector_versions
            WHERE connector_id=%s
            """,
            (record.connector_id,),
        ).fetchone()
        assert versions[0] == 1

        assert connector_repo.delete_for_scope("group", str(chat_id)) is True
        conn.execute("DELETE FROM chats WHERE telegram_chat_id=%s", (chat_id,))
        conn.commit()
