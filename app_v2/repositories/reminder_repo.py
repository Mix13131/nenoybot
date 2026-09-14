from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app_v2.domain.enums import ScopeType


@dataclass(frozen=True)
class ReminderRecord:
    id: int
    scope_type: ScopeType
    scope_id: str
    due_at: datetime
    status: str
    payload: dict[str, Any]
    last_fired_at: datetime | None


class ReminderRepository:
    def __init__(self, conn) -> None:
        self.conn = conn

    def create(self, *, scope_type: ScopeType, scope_id: str, due_at: datetime, payload: dict[str, Any] | None = None, recurrence_rule: str | None = None) -> ReminderRecord:
        if due_at.tzinfo is None or due_at.utcoffset() is None:
            raise ValueError("due_at must be timezone-aware")
        if recurrence_rule:
            raise NotImplementedError("Recurring reminders are not supported in v2 MVP yet")
        row = self.conn.execute(
            """
            INSERT INTO reminders(scope_type, scope_id, due_at, recurrence_rule, status, payload)
            VALUES (%s,%s,%s,NULL,'pending',%s::jsonb)
            RETURNING id, scope_type, scope_id, due_at, status, payload, last_fired_at
            """,
            (scope_type.value, scope_id, due_at, json.dumps(payload or {}, ensure_ascii=False)),
        ).fetchone()
        self.conn.commit()
        return self._row(row)

    def cancel(self, reminder_id: int) -> bool:
        row = self.conn.execute(
            "UPDATE reminders SET status='cancelled', updated_at=CURRENT_TIMESTAMP WHERE id=%s AND status IN ('pending','retry') RETURNING id",
            (reminder_id,),
        ).fetchone()
        self.conn.commit()
        return row is not None

    def reschedule(self, reminder_id: int, due_at: datetime) -> ReminderRecord | None:
        if due_at.tzinfo is None or due_at.utcoffset() is None:
            raise ValueError("due_at must be timezone-aware")
        row = self.conn.execute(
            """
            UPDATE reminders
            SET due_at=%s, status='pending', last_error=NULL, updated_at=CURRENT_TIMESTAMP
            WHERE id=%s AND status IN ('pending','retry')
            RETURNING id, scope_type, scope_id, due_at, status, payload, last_fired_at
            """,
            (due_at, reminder_id),
        ).fetchone()
        self.conn.commit()
        return self._row(row) if row else None

    def fire_due_once(self) -> ReminderRecord | None:
        """Atomically turn one due reminder into a durable reminder_due event."""
        row = self.conn.execute(
            """
            SELECT id, scope_type, scope_id, due_at, status, payload, last_fired_at
            FROM reminders
            WHERE status IN ('pending','retry') AND due_at <= CURRENT_TIMESTAMP
            ORDER BY due_at, id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            self.conn.commit()
            return None

        reminder = self._row(row)
        actor_user_id = reminder.payload.get("actor_user_id")
        text = reminder.payload.get("text") or reminder.payload.get("title") or "Напоминание"
        event_id = f"reminder:{reminder.id}:{reminder.due_at.isoformat()}"
        envelope_payload = {
            "event_id": event_id,
            "event_type": "reminder_due",
            "occurred_at": reminder.due_at.isoformat(),
            "scope_type": reminder.scope_type.value,
            "scope_id": reminder.scope_id,
            "actor_user_id": str(actor_user_id) if actor_user_id is not None else None,
            "message_id": None,
            "reply_to_message_id": None,
            "text": str(text),
            "metadata": {"reminder_id": reminder.id, "reminder_payload": reminder.payload},
        }
        self.conn.execute(
            """
            INSERT INTO events(event_id, event_type, scope_type, scope_id, actor_user_id, payload, status, available_at)
            VALUES (%s,'reminder_due',%s,%s,%s,%s::jsonb,'pending',CURRENT_TIMESTAMP)
            ON CONFLICT (event_id) DO NOTHING
            """,
            (event_id, reminder.scope_type.value, reminder.scope_id, str(actor_user_id) if actor_user_id is not None else None, json.dumps(envelope_payload, ensure_ascii=False)),
        )
        self.conn.execute(
            """
            UPDATE reminders
            SET status='sent', last_fired_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP, last_error=NULL
            WHERE id=%s
            """,
            (reminder.id,),
        )
        self.conn.commit()
        return ReminderRecord(reminder.id, reminder.scope_type, reminder.scope_id, reminder.due_at, "sent", reminder.payload, reminder.last_fired_at)

    @staticmethod
    def _row(row) -> ReminderRecord:
        return ReminderRecord(int(row[0]), ScopeType(row[1]), str(row[2]), row[3], str(row[4]), dict(row[5] or {}), row[6])
