from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

try:
    from .config import AppConfig
except ImportError:  # Allows direct script imports in local checks.
    from config import AppConfig


class MemoryStore(Protocol):
    def ensure_schema(self) -> None: ...

    def get_goal(self, chat_id: int) -> str | None: ...

    def set_goal(self, chat_id: int, goal: str) -> None: ...

    def clear_goal(self, chat_id: int) -> None: ...

    def get_summary(self, chat_id: int) -> str: ...

    def append_message(self, chat_id: int, role: str, content: str) -> None: ...

    def recent_messages(self, chat_id: int, limit: int = 10) -> list[tuple[str, str]]: ...


@dataclass
class InMemoryStore:
    goals: dict[int, str] = field(default_factory=dict)
    summaries: dict[int, str] = field(default_factory=dict)
    messages: dict[int, list[tuple[str, str]]] = field(default_factory=dict)

    def ensure_schema(self) -> None:
        return None

    def get_goal(self, chat_id: int) -> str | None:
        return self.goals.get(chat_id) or AppConfig.default_goal

    def set_goal(self, chat_id: int, goal: str) -> None:
        self.goals[chat_id] = goal

    def clear_goal(self, chat_id: int) -> None:
        self.goals.pop(chat_id, None)

    def get_summary(self, chat_id: int) -> str:
        return self.summaries.get(chat_id, "")

    def append_message(self, chat_id: int, role: str, content: str) -> None:
        chat_messages = self.messages.setdefault(chat_id, [])
        chat_messages.append((role, content))
        del chat_messages[:-20]

    def recent_messages(self, chat_id: int, limit: int = 10) -> list[tuple[str, str]]:
        return self.messages.get(chat_id, [])[-limit:]


class PostgresMemoryStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self):
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - exercised only without dependency.
            raise RuntimeError("psycopg is required when DATABASE_URL is set.") from exc

        return psycopg.connect(self.database_url, autocommit=True)

    def ensure_schema(self) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS nenoy_user_state (
                        chat_id BIGINT PRIMARY KEY,
                        goal TEXT,
                        summary TEXT NOT NULL DEFAULT '',
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS nenoy_messages (
                        id BIGSERIAL PRIMARY KEY,
                        chat_id BIGINT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_nenoy_messages_chat_id_id
                    ON nenoy_messages (chat_id, id DESC)
                    """
                )

    def get_goal(self, chat_id: int) -> str | None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT goal FROM nenoy_user_state WHERE chat_id = %s",
                    (chat_id,),
                )
                row = cursor.fetchone()
        return (row[0] if row and row[0] else None) or AppConfig.default_goal

    def set_goal(self, chat_id: int, goal: str) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO nenoy_user_state (chat_id, goal, updated_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (chat_id)
                    DO UPDATE SET goal = EXCLUDED.goal, updated_at = NOW()
                    """,
                    (chat_id, goal),
                )

    def clear_goal(self, chat_id: int) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO nenoy_user_state (chat_id, goal, updated_at)
                    VALUES (%s, NULL, NOW())
                    ON CONFLICT (chat_id)
                    DO UPDATE SET goal = NULL, updated_at = NOW()
                    """,
                    (chat_id,),
                )

    def get_summary(self, chat_id: int) -> str:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT summary FROM nenoy_user_state WHERE chat_id = %s",
                    (chat_id,),
                )
                row = cursor.fetchone()
        return row[0] if row and row[0] else ""

    def append_message(self, chat_id: int, role: str, content: str) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO nenoy_messages (chat_id, role, content)
                    VALUES (%s, %s, %s)
                    """,
                    (chat_id, role, content),
                )

    def recent_messages(self, chat_id: int, limit: int = 10) -> list[tuple[str, str]]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT role, content
                    FROM nenoy_messages
                    WHERE chat_id = %s
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (chat_id, limit),
                )
                rows = cursor.fetchall()
        return [(role, content) for role, content in reversed(rows)]


def create_memory_store() -> MemoryStore:
    if AppConfig.database_url:
        store: MemoryStore = PostgresMemoryStore(AppConfig.database_url)
    else:
        store = InMemoryStore()

    store.ensure_schema()
    return store
