from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app_v2.db.migrations import run_migrations
from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.repositories.reminder_repo import ReminderRepository
from app_v2.services.calendar_schedule import CalendarSchedule
from app_v2.services.group_reminders import GroupReminderService


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


def test_pending_calendar_intent_ttl_topic_and_source_provenance_postgres():
    url = os.getenv("NENOY_V2_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NENOY_V2_TEST_DATABASE_URL is not configured")
    psycopg = pytest.importorskip("psycopg")
    run_migrations(url)

    scope_id = f"calendar-flow-{uuid.uuid4().hex}"
    actor = "991234567"
    source_event_id = f"tg:{uuid.uuid4().hex}"
    reply_event_id = f"tg:{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc)

    def event(*, event_id: str, text: str, event_type: EventType, thread_id: int, message_id: str):
        return EventEnvelope(
            event_id=event_id,
            event_type=event_type,
            occurred_at=now,
            scope_type=ScopeType.GROUP,
            scope_id=scope_id,
            actor_user_id=actor,
            message_id=message_id,
            reply_to_message_id="900" if event_type is EventType.REPLY_TO_BOT else None,
            text=text,
            metadata={"message_thread_id": thread_id},
        )

    with psycopg.connect(url) as conn:
        repo = ReminderRepository(conn)
        service = GroupReminderService(repo)

        first = service.maybe_schedule(
            event(
                event_id=source_event_id,
                text="НеНой, напоминай каждый день в 9:00",
                event_type=EventType.DIRECT_MENTION,
                thread_id=777,
                message_id="701",
            ),
            now=now,
        )
        assert first.reason == "timezone_required"
        pending_payload = conn.execute(
            "SELECT payload FROM pending_calendar_intents WHERE source_event_id=%s",
            (source_event_id,),
        ).fetchone()[0]
        assert datetime.fromisoformat(pending_payload["reference_at"]) == now

        wrong_topic = service.maybe_schedule(
            event(
                event_id=f"tg:{uuid.uuid4().hex}",
                text="Europe/Berlin",
                event_type=EventType.REPLY_TO_BOT,
                thread_id=778,
                message_id="702",
            ),
            now=now,
        )
        assert wrong_topic is None

        completed = service.maybe_schedule(
            event(
                event_id=reply_event_id,
                text="по московскому времени",
                event_type=EventType.REPLY_TO_BOT,
                thread_id=777,
                message_id="703",
            ),
            now=now,
        )
        assert completed.status == "scheduled"

        row = conn.execute(
            """SELECT payload, status FROM reminders
               WHERE payload ->> 'source_event_id'=%s""",
            (source_event_id,),
        ).fetchone()
        assert row is not None
        payload = dict(row[0])
        assert payload["message_thread_id"] == 777
        assert payload["source_message_id"] == "701"
        assert payload["source_event_id"] == source_event_id

        pending_status = conn.execute(
            "SELECT status FROM pending_calendar_intents WHERE source_event_id=%s",
            (source_event_id,),
        ).fetchone()[0]
        assert pending_status == "completed"

        reminder_id = conn.execute(
            "SELECT id FROM reminders WHERE payload ->> 'source_event_id'=%s",
            (source_event_id,),
        ).fetchone()[0]
        conn.execute("DELETE FROM events WHERE event_id LIKE %s", (f"reminder:{reminder_id}:%",))
        conn.execute("DELETE FROM reminders WHERE id=%s", (reminder_id,))
        conn.execute("DELETE FROM pending_calendar_intents WHERE source_event_id=%s", (source_event_id,))
        conn.commit()


def test_expired_pending_calendar_intent_is_not_replayed_postgres():
    url = os.getenv("NENOY_V2_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NENOY_V2_TEST_DATABASE_URL is not configured")
    psycopg = pytest.importorskip("psycopg")
    run_migrations(url)

    scope_id = f"calendar-expired-{uuid.uuid4().hex}"
    actor = "991234568"
    source_event_id = f"tg:{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc)

    with psycopg.connect(url) as conn:
        repo = ReminderRepository(conn)
        repo.save_pending_calendar_intent(
            scope_id=scope_id,
            actor_user_id=actor,
            source_event_id=source_event_id,
            payload={
                "frequency": "daily",
                "hour": 9,
                "minute": 0,
                "subject": "synthetic",
                "source_message_id": "801",
                "message_thread_id": 888,
            },
        )
        conn.execute(
            """UPDATE pending_calendar_intents
               SET created_at=CURRENT_TIMESTAMP - INTERVAL '25 hours'
               WHERE source_event_id=%s""",
            (source_event_id,),
        )
        conn.commit()

        service = GroupReminderService(repo)
        reply = EventEnvelope(
            event_id=f"tg:{uuid.uuid4().hex}",
            event_type=EventType.REPLY_TO_BOT,
            occurred_at=now,
            scope_type=ScopeType.GROUP,
            scope_id=scope_id,
            actor_user_id=actor,
            message_id="802",
            reply_to_message_id="800",
            text="Europe/Moscow",
            metadata={"message_thread_id": 888},
        )
        action = service.maybe_schedule(reply, now=now)
        assert action is None
        assert conn.execute(
            "SELECT count(*) FROM reminders WHERE payload ->> 'source_event_id'=%s",
            (source_event_id,),
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT status FROM pending_calendar_intents WHERE source_event_id=%s",
            (source_event_id,),
        ).fetchone()[0] == "pending"

        conn.execute("DELETE FROM pending_calendar_intents WHERE source_event_id=%s", (source_event_id,))
        conn.commit()


def test_calendar_target_stop_condition_can_cancel_on_response_postgres():
    url = os.getenv("NENOY_V2_TEST_DATABASE_URL")
    if not url:
        pytest.skip("NENOY_V2_TEST_DATABASE_URL is not configured")
    psycopg = pytest.importorskip("psycopg")
    run_migrations(url)

    scope_id = f"calendar-target-{uuid.uuid4().hex}"
    source_event_id = f"tg:{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc)
    event = EventEnvelope(
        event_id=source_event_id,
        event_type=EventType.DIRECT_MENTION,
        occurred_at=now,
        scope_type=ScopeType.GROUP,
        scope_id=scope_id,
        actor_user_id="991234569",
        message_id="901",
        reply_to_message_id=None,
        text="НеНой, напоминай @alice каждый день в 19:00 Europe/Moscow, пока она не ответит",
        metadata={},
    )

    with psycopg.connect(url) as conn:
        repo = ReminderRepository(conn)
        service = GroupReminderService(repo)
        action = service.maybe_schedule(event, now=now)
        assert action.status == "scheduled"
        assert action.target_username == "alice"
        assert action.stop_on_reply is True

        row = conn.execute(
            "SELECT id, payload, status FROM reminders WHERE payload ->> 'source_event_id'=%s",
            (source_event_id,),
        ).fetchone()
        assert row is not None
        reminder_id = int(row[0])
        payload = dict(row[1])
        assert payload["target_username"] == "alice"
        assert payload["stop_on_reply"] is True

        cancelled = repo.cancel_waiting_for_response(
            scope_id=scope_id,
            actor_user_id=None,
            actor_username="alice",
        )
        assert cancelled == 1
        assert conn.execute("SELECT status FROM reminders WHERE id=%s", (reminder_id,)).fetchone()[0] == "cancelled"

        conn.execute("DELETE FROM reminders WHERE id=%s", (reminder_id,))
        conn.commit()
