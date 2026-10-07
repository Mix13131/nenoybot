from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from app_v2.db.migrations import run_migrations
from app_v2.repositories.ingest_repo import TelegramIngestRepository
from app_v2.repositories.participant_directory_repo import ParticipantDirectoryRepository


def _database_url() -> str:
    url = os.getenv("NENOY_V2_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NENOY_V2_TEST_DATABASE_URL is not configured")
    pytest.importorskip("psycopg")
    return url


def test_participant_directory_ordering_aliases_isolation_and_compatibility_postgres():
    url = _database_url()
    import psycopg

    run_migrations(url)
    now = datetime(2026, 2, 3, 12, tzinfo=timezone.utc)
    chat_a, chat_b = -910046001, -910046002
    user_one, user_two = 910046011, 910046012
    with psycopg.connect(url) as conn:
        conn.execute("DELETE FROM chats WHERE telegram_chat_id IN (%s, %s)", (chat_a, chat_b))
        conn.execute("DELETE FROM users WHERE telegram_user_id IN (%s, %s)", (user_one, user_two))
        repo = TelegramIngestRepository(conn)
        first_id = repo.upsert_user(
            {"id": user_one, "first_name": "Алиса", "username": "alice"},
            observed_at=now, observed_update_id=100,
        )
        second_id = repo.upsert_user(
            {"id": user_two, "first_name": "Борис", "username": "boris"},
            observed_at=now, observed_update_id=101,
        )
        internal_a = repo.upsert_chat({"id": chat_a, "type": "supergroup", "title": "Synthetic A"})
        internal_b = repo.upsert_chat({"id": chat_b, "type": "supergroup", "title": "Synthetic B"})
        repo.upsert_member(
            internal_a, first_id,
            user={"id": user_one, "first_name": "Алиса", "username": "alice"},
            observed_at=now, observed_update_id=100,
        )
        repo.upsert_member(
            internal_a, second_id,
            user={"id": user_two, "first_name": "Борис", "username": "shared"},
            observed_at=now, observed_update_id=101,
        )
        repo.upsert_member(
            internal_b, first_id,
            user={"id": user_one, "first_name": "Секретное имя", "username": "elsewhere"},
            observed_at=now + timedelta(minutes=1), observed_update_id=110,
        )

        # A newer rename is current and preserves the old username; an older replay
        # may extend first_seen but must not regress current identity or last_seen.
        repo.upsert_member(
            internal_a, first_id,
            user={"id": user_one, "first_name": "Алиса Новая", "username": "shared"},
            observed_at=now + timedelta(hours=1), observed_update_id=200,
        )
        repo.upsert_member(
            internal_a, first_id,
            user={"id": user_one, "first_name": "Старая", "username": "stale"},
            observed_at=now - timedelta(days=1), observed_update_id=50,
        )
        # Explicitly observed absence removes current username without losing aliases.
        same_second = now + timedelta(hours=2)
        repo.upsert_member(
            internal_a, first_id,
            user={"id": user_one, "first_name": "Алиса Новая"},
            observed_at=same_second, observed_update_id=300, username_observed=True,
        )
        # Within the same Telegram second, update_id is the deterministic tie-breaker.
        repo.upsert_member(
            internal_a, first_id,
            user={"id": user_one, "first_name": "Алиса Финал"},
            observed_at=same_second, observed_update_id=302, username_observed=False,
        )
        repo.upsert_member(
            internal_a, first_id,
            user={"id": user_one, "first_name": "Старая в ту же секунду", "username": "stale_same_second"},
            observed_at=same_second, observed_update_id=301, username_observed=True,
        )
        # A partial observation does not mean username removal.
        repo.upsert_member(
            internal_a, second_id,
            user={"id": user_two, "first_name": "Борис"},
            observed_at=same_second, observed_update_id=303, username_observed=False,
        )

        # Global user identity uses the same timestamp/update-id ordering contract.
        repo.upsert_user(
            {"id": user_one, "first_name": "Глобально Новая", "username": "global_new"},
            observed_at=same_second, observed_update_id=500,
        )
        repo.upsert_user(
            {"id": user_one, "first_name": "Глобально Старая", "username": "global_stale"},
            observed_at=same_second, observed_update_id=499,
        )
        conn.commit()

        directory = ParticipantDirectoryRepository(conn)
        listed = directory.list_recent(str(chat_a))
        assert [item.label for item in listed] == ["Алиса Финал", "Борис"]
        assert all("Секретное" not in item.label for item in listed)
        assert listed[0].first_seen_at == now - timedelta(days=1)
        assert listed[0].last_seen_at == now + timedelta(hours=2)
        assert directory.resolve_alias(str(chat_a), "alice").status == "found"
        assert directory.resolve_alias(str(chat_a), "shared").status == "ambiguous"
        assert directory.resolve_alias(str(chat_a), "elsewhere").status == "not_found"

        for index in range(20):
            repo.upsert_member(
                internal_a, first_id,
                user={"id": user_one, "first_name": f"Имя {index}"},
                observed_at=now + timedelta(hours=3, minutes=index),
                observed_update_id=400 + index,
                username_observed=False,
            )

        row = conn.execute(
            "SELECT role, participant_profile, current_username, jsonb_array_length(aliases), identity_observed_update_id FROM chat_members WHERE chat_id=%s AND user_id=%s",
            (internal_a, first_id),
        ).fetchone()
        assert row[0] == "member" and row[1] == {} and row[2] is None and row[3] <= 12
        assert row[4] == 419

        user_row = conn.execute(
            "SELECT display_name, username, identity_observed_update_id FROM users WHERE id=%s",
            (first_id,),
        ).fetchone()
        assert user_row == ("Глобально Новая", "global_new", 500)

        conn.execute("DELETE FROM chats WHERE telegram_chat_id IN (%s, %s)", (chat_a, chat_b))
        conn.execute("DELETE FROM users WHERE telegram_user_id IN (%s, %s)", (user_one, user_two))
        conn.commit()

