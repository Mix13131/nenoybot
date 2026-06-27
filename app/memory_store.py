from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

try:
    from .config import AppConfig
    from .work_blocks import WorkBlock
except ImportError:  # Allows direct script imports in local checks.
    from config import AppConfig
    from work_blocks import WorkBlock


class MemoryStore(Protocol):
    def ensure_schema(self) -> None: ...

    def get_goal(self, chat_id: int) -> str | None: ...

    def set_goal(self, chat_id: int, goal: str) -> None: ...

    def clear_goal(self, chat_id: int) -> None: ...

    def get_summary(self, chat_id: int) -> str: ...

    def append_message(self, chat_id: int, role: str, content: str) -> None: ...

    def recent_messages(self, chat_id: int, limit: int = 10) -> list[tuple[str, str]]: ...

    def add_reminder(
        self,
        chat_id: int,
        task_text: str,
        due_at: datetime,
        reminder_type: str = "checkin",
        work_block_id: str | None = None,
    ) -> int: ...

    def due_reminders(self, now: datetime, limit: int = 10) -> list["ScheduledEvent"]: ...

    def pending_reminders(self, chat_id: int, limit: int = 50) -> list["ScheduledEvent"]: ...

    def mark_reminder_sent(self, reminder_id: int) -> None: ...

    def cancel_previous_checkins_for_task(self, chat_id: int, task_text: str | None = None) -> None: ...

    def list_active_work_blocks(self, chat_id: int) -> list[WorkBlock]: ...

    def upsert_work_block(self, block: WorkBlock) -> WorkBlock: ...

    def get_work_block(self, chat_id: int, block_id: str) -> WorkBlock | None: ...


@dataclass(frozen=True)
class ScheduledEvent:
    id: int
    chat_id: int
    task_text: str
    event_type: str
    due_at: datetime
    work_block_id: str | None = None

    @property
    def reminder_type(self) -> str:
        return self.event_type


@dataclass
class InMemoryStore:
    goals: dict[int, str] = field(default_factory=dict)
    summaries: dict[int, str] = field(default_factory=dict)
    messages: dict[int, list[tuple[str, str]]] = field(default_factory=dict)
    reminders: dict[int, ScheduledEvent] = field(default_factory=dict)
    sent_reminders: set[int] = field(default_factory=set)
    next_reminder_id: int = 1
    work_blocks: dict[str, WorkBlock] = field(default_factory=dict)

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

    def add_reminder(
        self,
        chat_id: int,
        task_text: str,
        due_at: datetime,
        reminder_type: str = "checkin",
        work_block_id: str | None = None,
    ) -> int:
        reminder_id = self.next_reminder_id
        self.next_reminder_id += 1
        self.reminders[reminder_id] = ScheduledEvent(
            id=reminder_id,
            chat_id=chat_id,
            task_text=task_text,
            event_type=reminder_type,
            due_at=due_at,
            work_block_id=work_block_id,
        )
        return reminder_id

    def cancel_previous_checkins_for_task(self, chat_id: int, task_text: str | None = None) -> None:
        cancel_ids = {
            reminder_id
            for reminder_id, reminder in self.reminders.items()
            if reminder.chat_id == chat_id
            and reminder.id not in self.sent_reminders
            and reminder.event_type == "checkin"
            and (
                task_text is None
                or reminder.task_text == task_text
                or task_text in reminder.task_text
                or reminder.task_text in task_text
            )
        }
        for reminder_id in cancel_ids:
            self.reminders.pop(reminder_id, None)

    def due_reminders(self, now: datetime, limit: int = 10) -> list[ScheduledEvent]:
        due = [
            reminder
            for reminder in self.reminders.values()
            if reminder.id not in self.sent_reminders and reminder.due_at <= now
        ]
        return sorted(due, key=lambda reminder: reminder.due_at)[:limit]

    def pending_reminders(self, chat_id: int, limit: int = 50) -> list[ScheduledEvent]:
        pending = [
            reminder
            for reminder in self.reminders.values()
            if reminder.chat_id == chat_id and reminder.id not in self.sent_reminders
        ]
        return sorted(pending, key=lambda reminder: reminder.due_at)[:limit]

    def mark_reminder_sent(self, reminder_id: int) -> None:
        self.sent_reminders.add(reminder_id)

    def list_active_work_blocks(self, chat_id: int) -> list[WorkBlock]:
        return [
            block
            for block in self.work_blocks.values()
            if block.chat_id == chat_id and block.active
        ]

    def upsert_work_block(self, block: WorkBlock) -> WorkBlock:
        self.work_blocks[block.id] = block
        return block

    def get_work_block(self, chat_id: int, block_id: str) -> WorkBlock | None:
        block = self.work_blocks.get(block_id)
        if block is None or block.chat_id != chat_id:
            return None
        return block


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
                cursor.execute(
                    """
                CREATE TABLE IF NOT EXISTS nenoy_reminders (
                        id BIGSERIAL PRIMARY KEY,
                        chat_id BIGINT NOT NULL,
                        task_text TEXT NOT NULL,
                        due_at TIMESTAMPTZ NOT NULL,
                        reminder_type TEXT NOT NULL DEFAULT 'checkin',
                        work_block_id TEXT,
                        status TEXT NOT NULL DEFAULT 'pending',
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        sent_at TIMESTAMPTZ
                    )
                    """
                )
                cursor.execute(
                    """
                    ALTER TABLE IF EXISTS nenoy_reminders
                    ADD COLUMN IF NOT EXISTS reminder_type TEXT NOT NULL DEFAULT 'checkin'
                    """
                )
                cursor.execute(
                    """
                    ALTER TABLE IF EXISTS nenoy_reminders
                    ADD COLUMN IF NOT EXISTS work_block_id TEXT
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_nenoy_reminders_due
                    ON nenoy_reminders (status, due_at)
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS nenoy_work_blocks (
                        id TEXT PRIMARY KEY,
                        chat_id BIGINT NOT NULL,
                        title TEXT NOT NULL,
                        domain TEXT NOT NULL,
                        aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
                        entities JSONB NOT NULL DEFAULT '[]'::jsonb,
                        active BOOLEAN NOT NULL DEFAULT TRUE,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_nenoy_work_blocks_chat_active
                    ON nenoy_work_blocks (chat_id, active)
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

    def add_reminder(
        self,
        chat_id: int,
        task_text: str,
        due_at: datetime,
        reminder_type: str = "checkin",
        work_block_id: str | None = None,
    ) -> int:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO nenoy_reminders (chat_id, task_text, due_at, reminder_type, work_block_id)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (chat_id, task_text, due_at, reminder_type, work_block_id),
                )
                row = cursor.fetchone()
        return int(row[0])

    def cancel_previous_checkins_for_task(self, chat_id: int, task_text: str | None = None) -> None:
        where_clause = "chat_id = %s AND status = 'pending' AND reminder_type = 'checkin'"
        params: tuple[object, ...] = (chat_id,)
        if task_text is not None:
            where_clause += (
                " AND (task_text = %s OR task_text ILIKE %s "
                "OR POSITION(LOWER(task_text) IN LOWER(CAST(%s AS text))) > 0)"
            )
            params = (chat_id, task_text, f"%{task_text}%", task_text)

        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE nenoy_reminders
                    SET status = 'cancelled', sent_at = NOW()
                    WHERE {where_clause}
                    """,
                    params,
                )

    def due_reminders(self, now: datetime, limit: int = 10) -> list[ScheduledEvent]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, chat_id, task_text, due_at, reminder_type, work_block_id
                    FROM nenoy_reminders
                    WHERE status = 'pending' AND due_at <= %s
                    ORDER BY due_at ASC
                    LIMIT %s
                    """,
                    (now, limit),
                )
                rows = cursor.fetchall()
        return [
            ScheduledEvent(
                id=int(reminder_id),
                chat_id=int(chat_id),
                task_text=task_text,
                event_type=reminder_type,
                due_at=due_at,
                work_block_id=work_block_id,
            )
            for reminder_id, chat_id, task_text, due_at, reminder_type, work_block_id in rows
        ]

    def pending_reminders(self, chat_id: int, limit: int = 50) -> list[ScheduledEvent]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, chat_id, task_text, due_at, reminder_type, work_block_id
                    FROM nenoy_reminders
                    WHERE chat_id = %s AND status = 'pending'
                    ORDER BY due_at ASC
                    LIMIT %s
                    """,
                    (chat_id, limit),
                )
                rows = cursor.fetchall()
        return [
            ScheduledEvent(
                id=int(reminder_id),
                chat_id=int(row_chat_id),
                task_text=task_text,
                event_type=reminder_type,
                due_at=due_at,
                work_block_id=work_block_id,
            )
            for reminder_id, row_chat_id, task_text, due_at, reminder_type, work_block_id in rows
        ]

    def mark_reminder_sent(self, reminder_id: int) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE nenoy_reminders
                    SET status = 'sent', sent_at = NOW()
                    WHERE id = %s
                    """,
                    (reminder_id,),
                )

    def list_active_work_blocks(self, chat_id: int) -> list[WorkBlock]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, chat_id, title, domain, aliases, entities, active
                    FROM nenoy_work_blocks
                    WHERE chat_id = %s AND active = TRUE
                    ORDER BY created_at ASC
                    """,
                    (chat_id,),
                )
                rows = cursor.fetchall()
        return [_work_block_from_row(row) for row in rows]

    def upsert_work_block(self, block: WorkBlock) -> WorkBlock:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO nenoy_work_blocks (id, chat_id, title, domain, aliases, entities, active)
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                    ON CONFLICT (id)
                    DO UPDATE SET
                        title = EXCLUDED.title,
                        domain = EXCLUDED.domain,
                        aliases = EXCLUDED.aliases,
                        entities = EXCLUDED.entities,
                        active = EXCLUDED.active
                    """,
                    (
                        block.id,
                        block.chat_id,
                        block.title,
                        block.domain,
                        json.dumps(block.aliases, ensure_ascii=False),
                        json.dumps(block.entities, ensure_ascii=False),
                        block.active,
                    ),
                )
        return block

    def get_work_block(self, chat_id: int, block_id: str) -> WorkBlock | None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, chat_id, title, domain, aliases, entities, active
                    FROM nenoy_work_blocks
                    WHERE chat_id = %s AND id = %s
                    """,
                    (chat_id, block_id),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        return _work_block_from_row(row)


def create_memory_store() -> MemoryStore:
    if AppConfig.database_url:
        store: MemoryStore = PostgresMemoryStore(AppConfig.database_url)
    else:
        store = InMemoryStore()

    try:
        store.ensure_schema()
    except Exception as exc:
        print(f"Memory store fallback: {type(exc).__name__}: {exc}")
        return InMemoryStore()

    return store


def _work_block_from_row(row) -> WorkBlock:
    block_id, chat_id, title, domain, aliases, entities, active = row
    return WorkBlock(
        id=str(block_id),
        chat_id=int(chat_id),
        title=str(title),
        domain=str(domain),
        aliases=_json_tuple(aliases),
        entities=_json_tuple(entities),
        active=bool(active),
    )


def _json_tuple(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(json.loads(value))
    return tuple(value)
