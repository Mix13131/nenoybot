from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

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
