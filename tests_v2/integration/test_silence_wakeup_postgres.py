from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from app_v2.db.migrations import run_migrations
from app_v2.repositories.group_silence_wakeup_repo import GroupSilenceWakeupRepository


def _database_url() -> str | None:
    return os.getenv("NENOY_V2_TEST_DATABASE_URL")


def test_postgres_silence_wakeup_candidate_and_event_are_durable():
    database_url = _database_url()
    if not database_url:
        pytest.skip("NENOY_V2_TEST_DATABASE_URL is not configured")

    psycopg = pytest.importorskip("psycopg")
    run_migrations(database_url)

    chat_id = -100910000032
    user_id = 910000032
    now = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)

    with psycopg.connect(database_url) as conn:
        conn.execute("DELETE FROM events WHERE scope_id=%s", (str(chat_id),))
        conn.execute("DELETE FROM chats WHERE telegram_chat_id=%s", (chat_id,))
        conn.execute("DELETE FROM users WHERE telegram_user_id=%s", (user_id,))
        user_row = conn.execute(
            "INSERT INTO users(telegram_user_id, display_name) VALUES (%s, 'Silence Test') RETURNING id",
            (user_id,),
        ).fetchone()
        chat_row = conn.execute(
            """
            INSERT INTO chats(
                telegram_chat_id, chat_type, title, group_profile,
                is_whitelisted, is_active
            )
            VALUES (
                %s, 'group', 'Silence Test',
                '{"profile":"friends","unsolicited_enabled":true,"initiative":3,'
                '"timezone":"Europe/Moscow","silence_wakeup_enabled":true}'::jsonb,
                TRUE, TRUE
            )
            RETURNING id
            """,
            (chat_id,),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO messages(
                chat_id, user_id, telegram_message_id, text,
                message_type, created_at
            )
            VALUES (%s, %s, 77, 'последняя реплика', 'text', %s)
            """,
            (chat_row[0], user_row[0], now - timedelta(hours=4)),
        )
        conn.commit()

        repo = GroupSilenceWakeupRepository(conn)
        candidates = repo.list_candidates()
        item = next(row for row in candidates if row.scope_id == str(chat_id))

        assert item.last_human_message_id > 0
        assert item.last_human_excerpt == "последняя реплика"
        assert item.last_successful_wakeup_message_id is None
        assert item.last_attempt_message_id is None

        assert repo.enqueue(
            item,
            now=now,
            silence_minutes=240,
        ) is True
        assert repo.enqueue(
            item,
            now=now,
            silence_minutes=240,
        ) is False

        row = conn.execute(
            """
            SELECT event_type, scope_type, scope_id, payload
            FROM events
            WHERE scope_id=%s AND event_type='group_silence_wakeup'
            """,
            (str(chat_id),),
        ).fetchone()
        assert row[0] == "group_silence_wakeup"
        assert row[1] == "group"
        assert row[2] == str(chat_id)
        assert row[3]["metadata"]["silence_wakeup"] is True
        assert row[3]["metadata"]["silence_minutes"] == 240
        assert row[3]["metadata"]["last_human_message_id"] == item.last_human_message_id
        assert repo.is_current_episode(str(chat_id), item.last_human_message_id) is True

        conn.execute(
            """
            INSERT INTO messages(
                chat_id, user_id, telegram_message_id, text,
                message_type, created_at
            )
            VALUES (%s, %s, 78, 'новая реплика', 'text', %s)
            """,
            (chat_row[0], user_row[0], now + timedelta(seconds=1)),
        )
        conn.commit()
        assert repo.is_current_episode(str(chat_id), item.last_human_message_id) is False

        conn.execute("DELETE FROM events WHERE scope_id=%s", (str(chat_id),))
        conn.execute("DELETE FROM chats WHERE telegram_chat_id=%s", (chat_id,))
        conn.execute("DELETE FROM users WHERE telegram_user_id=%s", (user_id,))
        conn.commit()
