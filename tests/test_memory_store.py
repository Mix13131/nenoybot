import app.memory_store as memory_store
from app.memory_store import InMemoryStore


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
