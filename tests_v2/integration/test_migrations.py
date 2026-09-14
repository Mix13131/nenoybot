from __future__ import annotations

import os
from pathlib import Path

import pytest

from app_v2.adapters.postgres import DatabaseConfigurationError, get_database_url
from app_v2.db.migrations import MigrationError, discover_migrations, run_migrations


def test_discover_migrations_orders_by_version(tmp_path: Path) -> None:
    (tmp_path / "0002_second.sql").write_text("SELECT 2;", encoding="utf-8")
    (tmp_path / "0001_first.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "README.md").write_text("ignored", encoding="utf-8")

    migrations = discover_migrations(tmp_path)

    assert [item.version for item in migrations] == [1, 2]
    assert [item.filename for item in migrations] == ["0001_first.sql", "0002_second.sql"]
    assert all(len(item.checksum) == 64 for item in migrations)


def test_duplicate_migration_versions_are_rejected(tmp_path: Path) -> None:
    (tmp_path / "0001_first.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "0001_again.sql").write_text("SELECT 2;", encoding="utf-8")

    with pytest.raises(MigrationError, match="дублирующиеся версии"):
        discover_migrations(tmp_path)


def test_missing_database_url_is_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NENOY_V2_DATABASE_URL", raising=False)

    with pytest.raises(DatabaseConfigurationError, match="NENOY_V2_DATABASE_URL"):
        get_database_url()


def _live_database_url() -> str | None:
    return os.getenv("NENOY_V2_TEST_DATABASE_URL")


@pytest.mark.integration
def test_live_postgres_migrations_are_repeat_safe() -> None:
    database_url = _live_database_url()
    if not database_url:
        pytest.skip("NENOY_V2_TEST_DATABASE_URL is not configured")

    pytest.importorskip("psycopg")

    first = run_migrations(database_url)
    second = run_migrations(database_url)

    assert second == []
    assert first in ([1], [])


@pytest.mark.integration
def test_live_postgres_schema_constraints_and_indexes() -> None:
    database_url = _live_database_url()
    if not database_url:
        pytest.skip("NENOY_V2_TEST_DATABASE_URL is not configured")

    psycopg = pytest.importorskip("psycopg")
    run_migrations(database_url)

    expected_tables = {
        "users",
        "chats",
        "chat_members",
        "messages",
        "events",
        "memory_cards",
        "memory_relations",
        "tasks",
        "reminders",
        "interventions",
        "feedback_events",
        "outbox",
        "llm_usage",
        "schema_migrations",
    }
    expected_indexes = {
        "idx_events_pending",
        "idx_reminders_pending",
        "idx_outbox_pending",
        "idx_memory_cards_scope_status",
        "idx_memory_cards_scope_type",
    }

    with psycopg.connect(database_url) as conn:
        table_rows = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()
        index_rows = conn.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"
        ).fetchall()

        assert expected_tables <= {row[0] for row in table_rows}
        assert expected_indexes <= {row[0] for row in index_rows}

        conn.execute("DELETE FROM users WHERE telegram_user_id IN (910000001, 910000002)")
        conn.execute("INSERT INTO users(telegram_user_id) VALUES (910000001)")
        conn.commit()

        with pytest.raises(psycopg.errors.UniqueViolation):
            with conn.transaction():
                conn.execute("INSERT INTO users(telegram_user_id) VALUES (910000001)")

        conn.execute("DELETE FROM users WHERE telegram_user_id = 910000001")
        conn.commit()
