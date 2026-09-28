from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app_v2.adapters.telegram_webhook import normalize_update
from app_v2.db.migrations import discover_migrations, run_migrations
from app_v2.deploy_prepare import LegacyReconciliationError, reconcile_legacy_group
from app_v2.domain.group_defaults import new_group_profile
from app_v2.repositories.group_context_repo import GroupContextRepository
from app_v2.repositories.ingest_repo import TelegramIngestRepository
from app_v2.services.connector_resolver import LegacyGroupConnectorResolver
from app_v2.services.event_ingestor import ingest_telegram_update
from app_v2.services.group_access import GroupAccessService


@pytest.fixture
def legacy_db(tmp_path: Path):
    url = os.getenv("NENOY_V2_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NENOY_V2_TEST_DATABASE_URL is not configured")
    pg = pytest.importorskip("psycopg")
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    schema = "onboarding_" + uuid4().hex
    with pg.connect(url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    scoped_url = make_conninfo(url, options=f"-c search_path={schema}")
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    for migration in discover_migrations():
        if migration.version < 7:
            (old_dir / migration.filename).write_bytes(migration.path.read_bytes())
    try:
        run_migrations(scoped_url, old_dir)
        with pg.connect(scoped_url) as conn:
            for number, title, allowed, active, profile in [
                (1, "fixture-pending", False, True, {"initiative": 2, "custom": "keep"}),
                (2, "fixture-allowed", True, True, {"initiative": 4}),
                (3, "fixture-inactive", False, False, {}),
                (4, "fixture-empty", False, True, {}),
                (5, "fixture-other", False, True, {}),
                (6, "fixture-inactive-allowed", True, False, {}),
            ]:
                conn.execute(
                    "INSERT INTO chats(telegram_chat_id, chat_type, title, is_whitelisted, is_active, group_profile, silent_until) VALUES (%s,'group',%s,%s,%s,%s::jsonb,%s)",
                    (-1009900 - number, title, allowed, active, json.dumps(profile), datetime(2030, 1, 1, tzinfo=timezone.utc)),
                )
            user_id = conn.execute("INSERT INTO users(telegram_user_id, display_name) VALUES (990001, 'Fixture') RETURNING id").fetchone()[0]
            chat_id = conn.execute("SELECT id FROM chats WHERE telegram_chat_id=-1009901").fetchone()[0]
            conn.execute("INSERT INTO chat_members(chat_id,user_id,participant_profile) VALUES (%s,%s,'{\"custom\":\"member\"}'::jsonb)", (chat_id, user_id))
        yield pg, scoped_url
    finally:
        with pg.connect(url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def snapshot(conn, chat_id):
    return conn.execute(
        "SELECT is_whitelisted,is_active,group_access_blocked,group_legacy_reconcile_pending,group_profile,silent_until FROM chats WHERE telegram_chat_id=%s", (chat_id,)
    ).fetchone()


def traffic(conn, chat_id, title="fixture", chat_type="supergroup"):
    return TelegramIngestRepository(conn).upsert_chat({"id": chat_id, "type": chat_type, "title": title})


def test_migration_preserves_all_historical_denials_and_inactive_rows(legacy_db):
    pg, url = legacy_db
    assert run_migrations(url) == [7]
    assert run_migrations(url) == []
    with pg.connect(url) as conn:
        for identifier in (-1009901, -1009903, -1009904, -1009905):
            before = snapshot(conn, identifier)
            assert before[0] is False and before[2:4] == (True, True)
            traffic(conn, identifier)
            assert snapshot(conn, identifier) == before
        assert snapshot(conn, -1009902)[:4] == (True, True, False, False)
        before = snapshot(conn, -1009906)
        traffic(conn, -1009906)
        assert snapshot(conn, -1009906) == before


def test_authorized_reconciliation_is_exact_preserves_state_and_cannot_replay_a_block(legacy_db):
    pg, url = legacy_db
    run_migrations(url)
    with pg.connect(url) as conn:
        original = snapshot(conn, -1009901)
        member = conn.execute("SELECT participant_profile FROM chat_members").fetchall()
        other = snapshot(conn, -1009905)
        conn.commit()
        with pytest.raises(LegacyReconciliationError, match="not_found"):
            reconcile_legacy_group(conn, exact_title="wrong")
        with pytest.raises(LegacyReconciliationError, match="id_mismatch"):
            reconcile_legacy_group(conn, exact_title="fixture-pending", telegram_chat_id="-555")
        result = reconcile_legacy_group(conn, exact_title="fixture-pending", telegram_chat_id="-1009901", initialize_empty_profile=True)
        assert result["status"] == "reconciled" and result["profile_initialized"] is False
        after = snapshot(conn, -1009901)
        assert after[:4] == (True, True, False, False)
        assert after[4:] == original[4:]
        assert conn.execute("SELECT participant_profile FROM chat_members").fetchall() == member
        assert snapshot(conn, -1009905) == other
        conn.commit()
        assert reconcile_legacy_group(conn, exact_title="fixture-pending")["status"] == "already_active"
        GroupContextRepository(conn).set_whitelisted("-1009901", False)
        traffic(conn, -1009901, "fixture-pending")
        conn.commit()
        with pytest.raises(LegacyReconciliationError, match="not_pending_legacy_denial"):
            reconcile_legacy_group(conn, exact_title="fixture-pending")
        assert snapshot(conn, -1009901)[:4] == (False, True, True, False)


def test_reconcile_fails_closed_for_duplicate_title_inactive_and_new_denial(legacy_db):
    pg, url = legacy_db
    run_migrations(url)
    with pg.connect(url) as conn:
        with pytest.raises(LegacyReconciliationError, match="inactive_group"):
            reconcile_legacy_group(conn, exact_title="fixture-inactive")
        conn.execute("UPDATE chats SET title='fixture-pending' WHERE telegram_chat_id=-1009905")
        conn.commit()
        with pytest.raises(LegacyReconciliationError, match="ambiguous_title"):
            reconcile_legacy_group(conn, exact_title="fixture-pending", telegram_chat_id="-1009901")
        GroupContextRepository(conn).set_whitelisted("-1009904", False)
        with pytest.raises(LegacyReconciliationError, match="not_pending_legacy_denial"):
            reconcile_legacy_group(conn, exact_title="fixture-empty")


def test_opt_in_empty_legacy_profile_gets_same_starting_defaults(legacy_db):
    pg, url = legacy_db
    run_migrations(url)
    with pg.connect(url) as conn:
        result = reconcile_legacy_group(conn, exact_title="fixture-empty", initialize_empty_profile=True)
        assert result["profile_initialized"] is True
        assert snapshot(conn, -1009904)[4] == new_group_profile()
        assert snapshot(conn, -1009904)[5] == datetime(2030, 1, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize("chat_type", ["group", "supergroup"])
def test_new_group_auto_activation_context_and_profile_survive_later_updates(legacy_db, chat_type):
    pg, url = legacy_db
    run_migrations(url)
    with pg.connect(url) as conn:
        traffic(conn, -1009980, "fixture-new", chat_type)
        state = snapshot(conn, -1009980)
        assert state[:4] == (True, True, False, False)
        assert state[4] == new_group_profile()
        context = GroupContextRepository(conn).load("-1009980", None)
        assert LegacyGroupConnectorResolver().resolve(context).behavior.unsolicited_enabled is True
        conn.execute("UPDATE chats SET group_profile='{\"custom\":true}'::jsonb, silent_until='2030-01-01T00:00:00Z' WHERE telegram_chat_id=-1009980")
        before = snapshot(conn, -1009980)
        traffic(conn, -1009980, "fixture-new")
        assert snapshot(conn, -1009980) == before
        for other_type, identifier in [("private", 990070), ("channel", -1009981)]:
            traffic(conn, identifier, chat_type=other_type)
            assert snapshot(conn, identifier)[0] is False


@pytest.mark.parametrize("disable_method", ["deactivate", "friends_disabled", "legacy_direct_write"])
def test_all_supported_denials_survive_ingest(legacy_db, disable_method):
    pg, url = legacy_db
    run_migrations(url)
    with pg.connect(url) as conn:
        traffic(conn, -1009980, "fixture-new")
        repo = GroupContextRepository(conn)
        if disable_method == "deactivate":
            repo.set_whitelisted("-1009980", False)
        elif disable_method == "friends_disabled":
            repo.configure_friends_test("-1009980", profile={"initiative": 1}, enabled=False)
        else:
            conn.execute("UPDATE chats SET is_whitelisted=FALSE WHERE telegram_chat_id=-1009980")
        before = snapshot(conn, -1009980)
        traffic(conn, -1009980, "fixture-new")
        assert snapshot(conn, -1009980) == before
        assert before[0] is False


def test_parallel_migration_runs_apply_transition_once(legacy_db):
    _, url = legacy_db
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run_migrations(url), range(2)))
    assert sorted(results, key=len) == [[], [7]]


def test_concurrent_duplicate_first_updates_have_one_chat_member_message_and_event(legacy_db):
    pg, url = legacy_db
    run_migrations(url)
    update = {
        "update_id": 990080,
        "message": {
            "message_id": 1, "date": 1720000000,
            "chat": {"id": -1009980, "type": "supergroup", "title": "fixture-new"},
            "from": {"id": 990081, "is_bot": False, "first_name": "Fixture"},
            "text": "НеНой, привет!",
        },
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: ingest_telegram_update(update, database_url=url), range(2)))
    assert sorted(result.status for result in results) == ["accepted", "duplicate"]
    with pg.connect(url) as conn:
        assert conn.execute("SELECT count(*) FROM chats WHERE telegram_chat_id=-1009980").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM events WHERE telegram_update_id=990080").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM messages").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM chat_members cm JOIN chats c ON cm.chat_id=c.id WHERE c.telegram_chat_id=-1009980").fetchone()[0] == 1
        event = normalize_update(update).envelope
        assert GroupAccessService(GroupContextRepository(conn)).evaluate(event).allowed is True
