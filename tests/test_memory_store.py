from datetime import datetime, timedelta, timezone

import app.memory_store as memory_store
from app.memory_store import InMemoryStore, PostgresMemoryStore


def test_in_memory_store_keeps_goal_and_messages() -> None:
    store = InMemoryStore()

    store.set_goal(123, "Запустить бота")
    store.append_message(123, "user", "Потом")
    store.append_message(123, "assistant", "Делай")

    assert store.get_goal(123) == "Запустить бота"
    assert store.recent_messages(123) == [("user", "Потом"), ("assistant", "Делай")]


def test_in_memory_store_clears_goal() -> None:
    store = InMemoryStore()

    store.set_goal(123, "Запустить бота")
    store.clear_goal(123)

    assert store.get_goal(123) is None


def test_in_memory_store_keeps_due_reminders() -> None:
    store = InMemoryStore()
    now = datetime(2026, 6, 24, 10, 0, tzinfo=timezone.utc)
    reminder_id = store.add_reminder(123, "проверить API", now - timedelta(minutes=1))

    due = store.due_reminders(now)

    assert len(due) == 1
    assert due[0].id == reminder_id
    assert due[0].task_text == "проверить API"

    store.mark_reminder_sent(reminder_id)

    assert store.due_reminders(now) == []


def test_cancel_previous_checkins_for_task_removes_pending() -> None:
    store = InMemoryStore()
    now = datetime(2026, 6, 24, 10, 0, tzinfo=timezone.utc)

    first = store.add_reminder(123, "проверить API", now)
    second = store.add_reminder(123, "проверить API", now)
    third = store.add_reminder(456, "другая задача", now)

    store.cancel_previous_checkins_for_task(123, "проверить API")

    assert first not in store.reminders
    assert second not in store.reminders
    assert third in store.reminders


def test_rescheduling_cancels_previous_checkin_only() -> None:
    store = InMemoryStore()
    now = datetime(2026, 6, 24, 10, 0, tzinfo=timezone.utc)
    first = store.add_reminder(123, "сделать вебхук", now, reminder_type="checkin")
    second = store.add_reminder(123, "сделать вебхук", now.replace(hour=11), reminder_type="checkin")
    reminder_only = store.add_reminder(123, "сделать вебхук", now.replace(hour=12), reminder_type="reminder")

    store.cancel_previous_checkins_for_task(123, "сделать вебхук")

    assert first not in store.reminders
    assert second not in store.reminders
    assert reminder_only in store.reminders
    assert reminder_only != second


def test_create_memory_store_falls_back_when_postgres_fails(monkeypatch) -> None:
    monkeypatch.setattr(memory_store.AppConfig, "database_url", "postgresql://broken")

    class BrokenPostgresStore:
        def __init__(self, _database_url: str) -> None:
            pass

        def ensure_schema(self) -> None:
            raise RuntimeError("db down")

    monkeypatch.setattr(memory_store, "PostgresMemoryStore", BrokenPostgresStore)

    store = memory_store.create_memory_store()

    assert isinstance(store, InMemoryStore)


def test_postgres_cancel_previous_checkins_uses_flexible_task_match() -> None:
    class FakeCursor:
        def __init__(self, on_execute) -> None:
            self.on_execute = on_execute

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return None

        def execute(self, query, params):
            self.on_execute(query, params)

    class FakeConnection:
        def __init__(self, on_execute) -> None:
            self._cursor = FakeCursor(on_execute)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return None

        def cursor(self):
            return self._cursor

    captured: dict[str, object] = {}

    def capture(query, params) -> None:
        captured["query"] = query
        captured["params"] = params

    store = PostgresMemoryStore("postgres://test")
    store._connect = lambda: FakeConnection(capture)  # type: ignore[method-assign]

    store.cancel_previous_checkins_for_task(123, "Сделать проверочный запуск API")

    query = str(captured["query"])
    params = captured["params"]

    assert "task_text ILIKE %s" in query
    assert "CONCAT(%s, task_text, %s)" in query
    assert params == (
        123,
        "Сделать проверочный запуск API",
        f"%Сделать проверочный запуск API%",
        "Сделать проверочный запуск API",
        "%",
        "%",
    )
