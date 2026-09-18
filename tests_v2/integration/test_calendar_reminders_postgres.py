from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app_v2.db.migrations import run_migrations
from app_v2.domain.enums import ScopeType
from app_v2.repositories.reminder_repo import ReminderRepository
from app_v2.services.calendar_schedule import CalendarSchedule


def test_calendar_persistence_dedupe_fire_and_next_occurrence():
    url = os.getenv("NENOY_V2_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NENOY_V2_TEST_DATABASE_URL is not configured")
    psycopg = pytest.importorskip("psycopg")
    run_migrations(url)
    source = "test:calendar:dedupe"
    with psycopg.connect(url) as conn:
        conn.execute("DELETE FROM events WHERE event_id LIKE 'reminder:%'")
        conn.execute("DELETE FROM reminders WHERE payload ->> 'source_event_id'=%s", (source,))
        conn.execute("DELETE FROM pending_calendar_intents WHERE source_event_id=%s", (source,))
        conn.commit()
        repo = ReminderRepository(conn)
        repo.save_pending_calendar_intent(scope_id="calendar-test", actor_user_id="42",
                                          source_event_id=source, payload={"frequency": "daily"})
        assert repo.get_pending_calendar_intent(scope_id="calendar-test", actor_user_id="42")
        schedule = CalendarSchedule("daily", 9, 0, "Europe/Moscow")
        due = datetime.now(timezone.utc) - timedelta(minutes=1)
        first = repo.create(scope_type=ScopeType.GROUP, scope_id="calendar-test", due_at=due,
                            recurrence_rule=schedule.encode(), source_event_id=source,
                            payload={"text": "synthetic", "max_occurrences": 2})
        duplicate = repo.create(scope_type=ScopeType.GROUP, scope_id="calendar-test", due_at=due,
                                recurrence_rule=schedule.encode(), source_event_id=source,
                                payload={"text": "synthetic"})
        assert duplicate.id == first.id and duplicate.already_existing
        fired = repo.fire_due_once()
        assert fired and fired.id == first.id and fired.status == "pending"
        assert fired.due_at > datetime.now(timezone.utc)
        assert fired.due_at.astimezone(__import__("zoneinfo").ZoneInfo("Europe/Moscow")).hour == 9
        count = conn.execute("SELECT count(*) FROM reminders WHERE payload ->> 'source_event_id'=%s", (source,)).fetchone()[0]
        assert count == 1
        conn.execute("DELETE FROM events WHERE event_id LIKE %s", (f"reminder:{first.id}:%",))
        conn.execute("DELETE FROM reminders WHERE id=%s", (first.id,))
        conn.execute("DELETE FROM pending_calendar_intents WHERE source_event_id=%s", (source,))
        conn.commit()


def test_delayed_calendar_fire_advances_directly_to_future():
    url = os.getenv("NENOY_V2_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NENOY_V2_TEST_DATABASE_URL is not configured")
    psycopg = pytest.importorskip("psycopg")
    run_migrations(url)
    source = f"test:calendar:delayed:{uuid.uuid4()}"
    with psycopg.connect(url) as conn:
        repo = ReminderRepository(conn)
        due = datetime.now(timezone.utc) - timedelta(days=5)
        reminder = repo.create(
            scope_type=ScopeType.GROUP, scope_id="calendar-delayed", due_at=due,
            recurrence_rule=CalendarSchedule("daily", 9, 0, "Europe/Moscow").encode(),
            source_event_id=source,
            payload={"text": "synthetic", "max_occurrences": 10},
        )
        fired = repo.fire_due_once()
        assert fired and fired.id == reminder.id
        assert fired.due_at > datetime.now(timezone.utc)
        assert repo.fire_due_once() is None
        conn.execute("DELETE FROM events WHERE event_id LIKE %s", (f"reminder:{reminder.id}:%",))
        conn.execute("DELETE FROM reminders WHERE id=%s", (reminder.id,))
        conn.commit()


def test_concurrent_source_event_creates_one_usable_reminder():
    url = os.getenv("NENOY_V2_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NENOY_V2_TEST_DATABASE_URL is not configured")
    psycopg = pytest.importorskip("psycopg")
    run_migrations(url)
    source = f"test:calendar:concurrent:{uuid.uuid4()}"
    barrier = threading.Barrier(2)

    def create_one():
        with psycopg.connect(url) as conn:
            barrier.wait()
            return ReminderRepository(conn).create(
                scope_type=ScopeType.GROUP, scope_id="calendar-concurrent",
                due_at=datetime.now(timezone.utc) + timedelta(hours=1),
                source_event_id=source, payload={"text": "synthetic"},
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(lambda _: create_one(), range(2)))
    assert records[0].id == records[1].id
    assert sorted(record.already_existing for record in records) == [False, True]
    with psycopg.connect(url) as conn:
        assert conn.execute(
            "SELECT count(*) FROM reminders WHERE payload ->> 'source_event_id'=%s", (source,)
        ).fetchone()[0] == 1
        conn.execute("DELETE FROM reminders WHERE payload ->> 'source_event_id'=%s", (source,))
        conn.commit()


def test_migration_0003_normalizes_legacy_source_event_duplicates():
    url = os.getenv("NENOY_V2_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NENOY_V2_TEST_DATABASE_URL is not configured")
    psycopg = pytest.importorskip("psycopg")
    schema = f"migration_0003_{uuid.uuid4().hex}"
    migration = Path("app_v2/db/migrations/0003_calendar_reminders.sql").read_text(encoding="utf-8")
    with psycopg.connect(url) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
        conn.execute(f'SET search_path TO "{schema}"')
        conn.execute(
            """CREATE TABLE reminders (
                   id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                   payload JSONB NOT NULL DEFAULT '{}'::jsonb
               )"""
        )
        conn.execute(
            "INSERT INTO reminders(payload) VALUES (%s::jsonb), (%s::jsonb)",
            ('{"source_event_id":"legacy-duplicate"}', '{"source_event_id":"legacy-duplicate"}'),
        )
        conn.execute(migration)
        rows = conn.execute(
            "SELECT id, payload ->> 'source_event_id' FROM reminders ORDER BY id"
        ).fetchall()
        assert rows[0][1] == "legacy-duplicate"
        assert rows[1][1] is None
        conn.execute("SET search_path TO public")
        conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
        conn.commit()
