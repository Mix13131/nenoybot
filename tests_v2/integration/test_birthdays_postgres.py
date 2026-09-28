from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from app_v2.adapters.telegram_profile import TelegramBirthdate
from app_v2.db.migrations import run_migrations
from app_v2.repositories.birthday_repo import BirthdayRepository


def database_url():
    return os.getenv("NENOY_V2_TEST_DATABASE_URL")


def test_birthday_repository_persists_group_scoped_profile_and_dedupes_due_event():
    url=database_url()
    if not url:
        pytest.skip("NENOY_V2_TEST_DATABASE_URL is not configured")

    psycopg=pytest.importorskip("psycopg")
    run_migrations(url)

    chat_id=-919927001
    user_id=919927001
    now=datetime(2026,9,27,6,15,tzinfo=timezone.utc)

    with psycopg.connect(url) as conn:
        conn.execute("DELETE FROM interventions WHERE scope_id=%s", (str(chat_id),))
        conn.execute("DELETE FROM interventions WHERE scope_id=%s", (str(chat_id),))
        conn.execute("DELETE FROM events WHERE scope_id=%s", (str(chat_id),))
        conn.execute("DELETE FROM chats WHERE telegram_chat_id=%s", (chat_id,))
        conn.execute("DELETE FROM users WHERE telegram_user_id=%s", (user_id,))
        row=conn.execute(
            """
            INSERT INTO users(telegram_user_id, display_name)
            VALUES (%s, 'Birthday Test')
            RETURNING id
            """,
            (user_id,),
        ).fetchone()
        internal_user=int(row[0])
        row=conn.execute(
            """
            INSERT INTO chats(
                telegram_chat_id, chat_type, title,
                group_profile, is_whitelisted, is_active
            )
            VALUES (%s, 'group', 'Birthday Group',
                    '{"timezone":"Europe/Moscow"}'::jsonb, TRUE, TRUE)
            RETURNING id
            """,
            (chat_id,),
        ).fetchone()
        internal_chat=int(row[0])
        conn.execute(
            """
            INSERT INTO chat_members(chat_id, user_id, participant_profile)
            VALUES (%s, %s, '{"personality_modifiers":{"warmth":1}}'::jsonb)
            """,
            (internal_chat, internal_user),
        )
        conn.commit()

        repo=BirthdayRepository(conn)

        # Telegram-sourced data follows current visibility.
        assert repo.record_telegram_lookup(
            scope_id=str(chat_id),
            telegram_user_id=str(user_id),
            status="available",
            now=now,
            birthdate=TelegramBirthdate(day=8, month=6, year=None),
        ) is True
        telegram_profile=repo.get_participant_profile(str(chat_id), str(user_id))
        assert telegram_profile["birthday"]["source"] == "telegram_profile"
        assert telegram_profile["birthday"]["day"] == 8

        assert repo.record_telegram_lookup(
            scope_id=str(chat_id),
            telegram_user_id=str(user_id),
            status="not_shared",
            now=now,
        ) is True
        hidden_profile=repo.get_participant_profile(str(chat_id), str(user_id))
        assert "birthday" not in hidden_profile

        assert repo.save_explicit_birthday(
            scope_id=str(chat_id),
            telegram_user_id=str(user_id),
            day=27,
            month=9,
            year=1981,
            now=now,
        ) is True

        profile=repo.get_participant_profile(str(chat_id), str(user_id))
        assert profile is not None
        assert profile["personality_modifiers"]["warmth"] == 1
        assert profile["birthday"]["source"] == "explicit"
        assert profile["birthday"]["day"] == 27
        assert profile["birthday"]["year"] == 1981

        # Telegram refresh never overwrites an explicit user-provided birthday.
        assert repo.record_telegram_lookup(
            scope_id=str(chat_id),
            telegram_user_id=str(user_id),
            status="available",
            now=now,
            birthdate=TelegramBirthdate(day=1, month=1, year=2000),
        ) is True
        profile=repo.get_participant_profile(str(chat_id), str(user_id))
        assert profile["birthday"]["day"] == 27
        assert profile["birthday"]["month"] == 9

        assert repo.set_congratulations_enabled(
            scope_id=str(chat_id),
            telegram_user_id=str(user_id),
            enabled=False,
            now=now,
        ) is True
        assert not any(
            item.scope_id == str(chat_id)
            and item.telegram_user_id == str(user_id)
            for item in repo.list_candidates()
        )
        assert repo.set_congratulations_enabled(
            scope_id=str(chat_id),
            telegram_user_id=str(user_id),
            enabled=True,
            now=now,
        ) is True

        candidates=repo.list_candidates()
        candidate=next(
            item for item in candidates
            if item.scope_id == str(chat_id)
            and item.telegram_user_id == str(user_id)
        )
        assert candidate.display_name == "Birthday Test"

        conn.execute(
            """
            INSERT INTO interventions(
                event_id, scope_type, scope_id, primary_action, mode,
                intervention_score, reason_codes, policy_version,
                selected_memory_ids, generated_text, created_at
            )
            VALUES (
                NULL, 'group', %s, 'reply', 'group_direct_reply',
                100, '[]'::jsonb, 'test',
                '[]'::jsonb, %s, %s
            )
            """,
            (
                str(chat_id),
                "Справедливо. Birthday Test, с днём рождения! Праздничная амнистия.",
                now,
            ),
        )
        conn.commit()

        assert repo.already_congratulated_today(
            candidate,
            since=now.replace(hour=0, minute=0, second=0, microsecond=0),
        ) is True

        assert repo.enqueue_due(candidate, local_year=2026, now=now) is True
        assert repo.enqueue_due(candidate, local_year=2026, now=now) is False

        event=conn.execute(
            """
            SELECT event_id, event_type, payload
            FROM events
            WHERE scope_id=%s AND event_type='birthday_due'
            """,
            (str(chat_id),),
        ).fetchone()
        assert event is not None
        assert event[0] == f"birthday:{chat_id}:{user_id}:2026"
        assert event[1] == "birthday_due"
        assert event[2]["metadata"]["birthday_name"] == "Birthday Test"
        assert event[2]["metadata"]["age_allowed"] is False
        assert "year" not in event[2]["metadata"]

        assert repo.clear_birthday(
            scope_id=str(chat_id),
            telegram_user_id=str(user_id),
            now=now,
        ) is True
        cleared=repo.get_participant_profile(str(chat_id), str(user_id))
        assert "birthday" not in cleared
        assert cleared["birthday_discovery"]["enabled"] is False
        assert repo.telegram_refresh_due(
            scope_id=str(chat_id),
            telegram_user_id=str(user_id),
            now=now.replace(year=2027),
        ) is False

        conn.execute("DELETE FROM events WHERE scope_id=%s", (str(chat_id),))
        conn.execute("DELETE FROM chats WHERE telegram_chat_id=%s", (chat_id,))
        conn.execute("DELETE FROM users WHERE telegram_user_id=%s", (user_id,))
        conn.commit()
