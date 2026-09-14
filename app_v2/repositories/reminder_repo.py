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
    recurrence_rule: str | None = None


class ReminderRepository:
    def __init__(self, conn) -> None:
        self.conn = conn

    @staticmethod
    def _interval_seconds(recurrence_rule: str | None) -> int | None:
        if not recurrence_rule:
            return None
        prefix = "interval:"
        if not recurrence_rule.startswith(prefix):
            raise ValueError("Unsupported recurrence_rule")
        try:
            seconds = int(recurrence_rule[len(prefix):])
        except ValueError as exc:
            raise ValueError("Invalid interval recurrence_rule") from exc
        if seconds < 60 or seconds > 31 * 24 * 60 * 60:
            raise ValueError("Reminder interval must be between 60 seconds and 31 days")
        return seconds

    def create(
        self,
        *,
        scope_type: ScopeType,
        scope_id: str,
        due_at: datetime,
        payload: dict[str, Any] | None = None,
        recurrence_rule: str | None = None,
    ) -> ReminderRecord:
        if due_at.tzinfo is None or due_at.utcoffset() is None:
            raise ValueError("due_at must be timezone-aware")
        self._interval_seconds(recurrence_rule)
        row = self.conn.execute(
            """
            INSERT INTO reminders(scope_type, scope_id, due_at, recurrence_rule, status, payload)
            VALUES (%s,%s,%s,%s,'pending',%s::jsonb)
            RETURNING id, scope_type, scope_id, due_at, status, payload, last_fired_at, recurrence_rule
            """,
            (
                scope_type.value,
                scope_id,
                due_at,
                recurrence_rule,
                json.dumps(payload or {}, ensure_ascii=False),
            ),
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

    def cancel_waiting_for_response(
        self,
        *,
        scope_id: str,
        actor_user_id: str | None = None,
        actor_username: str | None = None,
    ) -> int:
        """Cancel recurring group reminders whose stop condition is this person's reply."""
        user_id = (actor_user_id or "").strip()
        username = (actor_username or "").strip().lstrip("@").lower()
        if not user_id and not username:
            return 0

        row = self.conn.execute(
            """
            WITH cancelled AS (
                UPDATE reminders
                SET status='cancelled', updated_at=CURRENT_TIMESTAMP, last_error=NULL
                WHERE scope_type='group'
                  AND scope_id=%s
                  AND status IN ('pending','retry')
                  AND COALESCE(payload ->> 'stop_on_reply', 'false') = 'true'
                  AND (
                      (%s <> '' AND COALESCE(payload ->> 'target_user_id', '') = %s)
                      OR
                      (%s <> '' AND lower(COALESCE(payload ->> 'target_username', '')) = %s)
                  )
                RETURNING id
            )
            SELECT count(*) FROM cancelled
            """,
            (scope_id, user_id, user_id, username, username),
        ).fetchone()
        self.conn.commit()
        return int(row[0]) if row else 0

    def reschedule(self, reminder_id: int, due_at: datetime) -> ReminderRecord | None:
        if due_at.tzinfo is None or due_at.utcoffset() is None:
            raise ValueError("due_at must be timezone-aware")
        row = self.conn.execute(
            """
            UPDATE reminders
            SET due_at=%s, status='pending', last_error=NULL, updated_at=CURRENT_TIMESTAMP
            WHERE id=%s AND status IN ('pending','retry')
            RETURNING id, scope_type, scope_id, due_at, status, payload, last_fired_at, recurrence_rule
            """,
            (due_at, reminder_id),
        ).fetchone()
        self.conn.commit()
        return self._row(row) if row else None

    def fire_due_once(self) -> ReminderRecord | None:
        """Atomically turn one due reminder into a durable reminder_due event."""
        row = self.conn.execute(
            """
            SELECT id, scope_type, scope_id, due_at, status, payload, last_fired_at, recurrence_rule
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
            "metadata": {
                "reminder_id": reminder.id,
                "reminder_payload": reminder.payload,
                "message_thread_id": reminder.payload.get("message_thread_id"),
            },
        }
        self.conn.execute(
            """
            INSERT INTO events(event_id, event_type, scope_type, scope_id, actor_user_id, payload, status, available_at)
            VALUES (%s,'reminder_due',%s,%s,%s,%s::jsonb,'pending',CURRENT_TIMESTAMP)
            ON CONFLICT (event_id) DO NOTHING
            """,
            (
                event_id,
                reminder.scope_type.value,
                reminder.scope_id,
                str(actor_user_id) if actor_user_id is not None else None,
                json.dumps(envelope_payload, ensure_ascii=False),
            ),
        )

        interval_seconds = self._interval_seconds(reminder.recurrence_rule)
        if interval_seconds is None:
            next_status = "sent"
            self.conn.execute(
                """
                UPDATE reminders
                SET status='sent', last_fired_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP, last_error=NULL
                WHERE id=%s
                """,
                (reminder.id,),
            )
            next_due = reminder.due_at
        else:
            next_status = "pending"
            next_row = self.conn.execute(
                """
                UPDATE reminders
                SET status='pending',
                    due_at=GREATEST(due_at, CURRENT_TIMESTAMP) + (%s * INTERVAL '1 second'),
                    last_fired_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP,
                    last_error=NULL
                WHERE id=%s
                RETURNING due_at
                """,
                (interval_seconds, reminder.id),
            ).fetchone()
            next_due = next_row[0] if next_row else reminder.due_at

        self.conn.commit()
        return ReminderRecord(
            reminder.id,
            reminder.scope_type,
            reminder.scope_id,
            next_due,
            next_status,
            reminder.payload,
            reminder.last_fired_at,
            reminder.recurrence_rule,
        )

    @staticmethod
    def _row(row) -> ReminderRecord:
        return ReminderRecord(
            int(row[0]),
            ScopeType(row[1]),
            str(row[2]),
            row[3],
            str(row[4]),
            dict(row[5] or {}),
            row[6],
            str(row[7]) if len(row) > 7 and row[7] is not None else None,
        )
