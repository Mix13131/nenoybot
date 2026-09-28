from __future__ import annotations

from app_v2.db.migrations import run_migrations
from app_v2.domain.group_defaults import new_group_profile
from tests_v2.integration.test_group_onboarding_postgres import legacy_db, snapshot, traffic


def test_old_writer_in_rolling_window_gets_group_defaults_without_overriding_denials(legacy_db):
    pg, url = legacy_db
    run_migrations(url)
    with pg.connect(url) as conn:
        # Exact old-writer INSERT: it knows neither whitelist nor new columns.
        conn.execute("INSERT INTO chats(telegram_chat_id,chat_type,title) VALUES (-1009988,'group','fixture-old-writer')")
        state = snapshot(conn, -1009988)
        assert state[:4] == (True, True, False, False)
        assert state[4] == new_group_profile()
        traffic(conn, -1009988, 'fixture-old-writer')
        assert snapshot(conn, -1009988) == state
        conn.execute("INSERT INTO chats(telegram_chat_id,chat_type,title) VALUES (990088,'private','fixture-private')")
        assert snapshot(conn, 990088)[0] is False
        assert snapshot(conn, 990088)[4] == {}
        # Explicit denial and existing historical denial both remain denied.
        conn.execute("INSERT INTO chats(telegram_chat_id,chat_type,title,is_whitelisted) VALUES (-1009989,'group','fixture-denied',FALSE)")
        traffic(conn, -1009989)
        assert snapshot(conn, -1009989)[0] is False
        conn.execute("UPDATE chats SET is_whitelisted=FALSE WHERE telegram_chat_id=-1009988")
        traffic(conn, -1009988)
        assert snapshot(conn, -1009988)[0] is False
        assert snapshot(conn, -1009901)[:4] == (False, True, True, True)
