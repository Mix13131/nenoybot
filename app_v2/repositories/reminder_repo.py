from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app_v2.domain.enums import ScopeType
from app_v2.services.calendar_schedule import CalendarSchedule, next_calendar_occurrence


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
    already_existing: bool = False


class ReminderRepository:
    def __init__(self, conn) -> None:
        self.conn = conn

    @staticmethod
    def _interval_seconds(recurrence_rule: str | None) -> int | None:
        if not recurrence_rule:
            return None
        prefix = "interval:"
        if recurrence_rule.startswith("calendar:"):
            CalendarSchedule.decode(recurrence_rule)
            return None
        if not recurrence_rule.startswith(prefix):
            raise ValueError("Unsupported recurrence_rule")
        try:
            seconds = int(recurrence_rule[len(prefix):])
        except ValueError as exc:
            raise ValueError("Invalid interval recurrence_rule") from exc
        if seconds < 60 or seconds > 31 * 24 * 60 * 60:
            raise ValueError("Reminder interval must be between 60 seconds and 31 days")
        return seconds

    def _cancel_queued_work(self, reminder_ids: list[int]) -> None:
        """Suppress due events/outbox rows already queued for cancelled reminders."""
        for reminder_id in reminder_ids:
            self.conn.execute(
                """
                UPDATE events
                SET status='completed', processed_at=CURRENT_TIMESTAMP,
                    last_error='reminder cancelled before delivery'
                WHERE event_id LIKE %s
                  AND status IN ('pending','retry')
                """,
                (f"reminder:{reminder_id}:%",),
            )
            self.conn.execute(
                """
                UPDATE outbox
                SET status='failed', last_error='reminder cancelled before delivery'
                WHERE dedupe_key LIKE %s
                  AND status IN ('pending','retry')
                """,
                (f"reply:reminder:{reminder_id}:%",),
            )

    def create(
        self,
        *,
        scope_type: ScopeType,
        scope_id: str,
        due_at: datetime,
        payload: dict[str, Any] | None = None,
        recurrence_rule: str | None = None,
        source_event_id: str | None = None,
    ) -> ReminderRecord:
        if due_at.tzinfo is None or due_at.utcoffset() is None:
            raise ValueError("due_at must be timezone-aware")
        self._interval_seconds(recurrence_rule)
        body = dict(payload or {})
        effective_source_event_id = source_event_id
        if effective_source_event_id is None:
            payload_source_event_id = body.get("source_event_id")
            if payload_source_event_id is not None:
                effective_source_event_id = str(payload_source_event_id)
        if effective_source_event_id is not None:
            body["source_event_id"] = effective_source_event_id
        row = self.conn.execute(
            """
            INSERT INTO reminders(scope_type, scope_id, due_at, recurrence_rule, status, payload)
            VALUES (%s,%s,%s,%s,'pending',%s::jsonb)
            ON CONFLICT ((payload ->> 'source_event_id'))
                WHERE payload ->> 'source_event_id' IS NOT NULL
                DO NOTHING
            RETURNING id, scope_type, scope_id, due_at, status, payload, last_fired_at, recurrence_rule
            """,
            (
                scope_type.value,
                scope_id,
                due_at,
                recurrence_rule,
                json.dumps(body, ensure_ascii=False),
            ),
        ).fetchone()
        already_existing = row is None
        if already_existing:
            row = self.conn.execute(
                """SELECT id, scope_type, scope_id, due_at, status, payload, last_fired_at, recurrence_rule
                   FROM reminders WHERE payload ->> 'source_event_id'=%s""",
                (effective_source_event_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Source-event conflict did not yield a reminder")
        self.conn.commit()
        record = self._row(row)
        if already_existing:
            return ReminderRecord(**{**record.__dict__, "already_existing": True})
        return record

    def save_pending_calendar_intent(self, *, scope_id: str, actor_user_id: str,
                                     source_event_id: str, payload: dict[str, Any]) -> None:
        self.conn.execute(
            """INSERT INTO pending_calendar_intents(scope_type,scope_id,actor_user_id,source_event_id,payload)
               VALUES ('group',%s,%s,%s,%s::jsonb) ON CONFLICT (source_event_id) DO NOTHING""",
            (scope_id, actor_user_id, source_event_id, json.dumps(payload, ensure_ascii=False)),
        )
        self.conn.commit()

    def get_pending_calendar_intent(
        self,
        *,
        scope_id: str,
        actor_user_id: str,
        message_thread_id: str | int | None = None,
        max_age_hours: int = 24,
    ):
        if max_age_hours <= 0:
            return None
        thread_id = None if message_thread_id is None else str(message_thread_id)
        row = self.conn.execute(
            """SELECT id, source_event_id, payload FROM pending_calendar_intents
               WHERE scope_type='group'
                 AND scope_id=%s
                 AND actor_user_id=%s
                 AND status='pending'
                 AND created_at >= CURRENT_TIMESTAMP - (%s * INTERVAL '1 hour')
                 AND (payload ->> 'message_thread_id') IS NOT DISTINCT FROM %s
               ORDER BY created_at DESC LIMIT 1""",
            (scope_id, actor_user_id, max_age_hours, thread_id),
        ).fetchone()
        return None if row is None else {"id": int(row[0]), "source_event_id": row[1], "payload": dict(row[2])}

    def complete_pending_calendar_intent(self, intent_id: int) -> None:
        self.conn.execute("UPDATE pending_calendar_intents SET status='completed', updated_at=CURRENT_TIMESTAMP WHERE id=%s", (intent_id,))
        self.conn.commit()

    def cancel(self, reminder_id: int) -> bool:
        row = self.conn.execute(
            """
            UPDATE reminders
            SET status='cancelled', updated_at=CURRENT_TIMESTAMP, last_error=NULL
            WHERE id=%s AND status IN ('pending','retry')
            RETURNING id
            """,
            (reminder_id,),
        ).fetchone()
        if row is not None:
            self._cancel_queued_work([int(row[0])])
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

        rows = self.conn.execute(
            """
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
            """,
            (scope_id, user_id, user_id, username, username),
        ).fetchall()
        ids = [int(row[0]) for row in rows]
        self._cancel_queued_work(ids)
        self.conn.commit()
        return len(ids)

    def cancel_reminder_from_bot_reply(
        self,
        *,
        scope_id: str,
        reply_to_message_id: str | None,
    ) -> int:
        """Cancel the exact active reminder chain whose Telegram ping was replied to."""
        reply_id = (reply_to_message_id or "").strip()
        if not reply_id.isdigit():
            return 0

        row = self.conn.execute(
            """
            SELECT r.id
            FROM outbox AS o
            JOIN reminders AS r
              ON (o.payload #>> '{metadata,event_id}') LIKE ('reminder:' || r.id::text || ':%')
            WHERE o.channel='telegram'
              AND o.destination_id=%s
              AND o.telegram_message_id=%s
              AND r.scope_type='group'
              AND r.scope_id=%s
              AND r.status IN ('pending','retry')
            ORDER BY o.id DESC
            LIMIT 1
            """,
            (scope_id, int(reply_id), scope_id),
        ).fetchone()
        if row is None:
            self.conn.commit()
            return 0

        reminder_id = int(row[0])
        cancelled = self.conn.execute(
            """
            UPDATE reminders
            SET status='cancelled', updated_at=CURRENT_TIMESTAMP, last_error=NULL
            WHERE id=%s AND status IN ('pending','retry')
            RETURNING id
            """,
            (reminder_id,),
        ).fetchone()
        if cancelled is None:
            self.conn.commit()
            return 0

        self._cancel_queued_work([reminder_id])
        self.conn.commit()
        return 1

    def cancel_active_group_reminders(
        self,
        *,
        scope_id: str,
        target_username: str | None = None,
    ) -> int:
        """Fail-safe group kill switch for active reminders.

        Anyone in the same group may stop reminder spam. If target_username is
        supplied, only chains aimed at that user are stopped; otherwise every
        active reminder in the group is cancelled.
        """
        target = (target_username or "").strip().lstrip("@").lower()
        conditions = [
            "scope_type='group'",
            "scope_id=%s",
            "status IN ('pending','retry')",
        ]
        params: list[object] = [scope_id]
        if target:
            conditions.append("lower(COALESCE(payload ->> 'target_username', '')) = %s")
            params.append(target)

        rows = self.conn.execute(
            f"""
            UPDATE reminders
            SET status='cancelled', updated_at=CURRENT_TIMESTAMP, last_error=NULL
            WHERE {' AND '.join(conditions)}
            RETURNING id
            """,
            tuple(params),
        ).fetchall()
        ids = [int(row[0]) for row in rows]
        self._cancel_queued_work(ids)
        self.conn.commit()
        return len(ids)

    def cancel_group_reminders(
        self,
        *,
        scope_id: str,
        creator_user_id: str | None,
        target_username: str | None = None,
    ) -> int:
        """Legacy creator-scoped cancellation retained for compatibility."""
        creator = (creator_user_id or "").strip()
        if not creator:
            return 0
        target = (target_username or "").strip().lstrip("@").lower()

        conditions = [
            "scope_type='group'",
            "scope_id=%s",
            "status IN ('pending','retry')",
            "COALESCE(payload ->> 'actor_user_id', '') = %s",
        ]
        params: list[object] = [scope_id, creator]
        if target:
            conditions.append("lower(COALESCE(payload ->> 'target_username', '')) = %s")
            params.append(target)

        rows = self.conn.execute(
            f"""
            UPDATE reminders
            SET status='cancelled', updated_at=CURRENT_TIMESTAMP, last_error=NULL
            WHERE {' AND '.join(conditions)}
            RETURNING id
            """,
            tuple(params),
        ).fetchall()
        ids = [int(row[0]) for row in rows]
        self._cancel_queued_work(ids)
        self.conn.commit()
        return len(ids)

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
            SELECT id, scope_type, scope_id, due_at, status, payload, last_fired_at, recurrence_rule,
                   clock_timestamp()
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
        processing_at = row[8]
        interval_seconds = self._interval_seconds(reminder.recurrence_rule)
        calendar = (
            CalendarSchedule.decode(reminder.recurrence_rule)
            if reminder.recurrence_rule and reminder.recurrence_rule.startswith("calendar:")
            else None
        )
        payload = dict(reminder.payload)
        fire_count = int(payload.get("fire_count") or 0) + 1
        payload["fire_count"] = fire_count

        if calendar is not None:
            # Calendar recurrence means the natural-language promise "every
            # day / weekday / Friday": keep the local-wall-clock chain alive
            # until an explicit existing cancellation control stops it.
            payload.pop("max_occurrences", None)
            payload["recurrence_policy"] = "until_cancelled"
            max_occurrences = None
        else:
            max_occurrences = int(
                payload.get("max_occurrences") or (4 if interval_seconds is not None else 1)
            )
            max_occurrences = max(1, min(max_occurrences, 100))
            payload["max_occurrences"] = max_occurrences

        actor_user_id = payload.get("actor_user_id")
        text = payload.get("text") or payload.get("title") or "Напоминание"
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
                "reminder_payload": payload,
                "message_thread_id": payload.get("message_thread_id"),
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

        if calendar is not None:
            stop_after_this_fire = False
        elif interval_seconds is None:
            stop_after_this_fire = True
        else:
            assert max_occurrences is not None
            stop_after_this_fire = fire_count >= max_occurrences

        if stop_after_this_fire:
            next_status = "sent"
            self.conn.execute(
                """
                UPDATE reminders
                SET status='sent', last_fired_at=CURRENT_TIMESTAMP,
                    payload=%s::jsonb,
                    updated_at=CURRENT_TIMESTAMP, last_error=NULL
                WHERE id=%s
                """,
                (json.dumps(payload, ensure_ascii=False), reminder.id),
            )
            next_due = reminder.due_at
        else:
            next_status = "pending"
            calendar_reference = max(reminder.due_at, processing_at)
            next_value = (next_calendar_occurrence(calendar, calendar_reference)
                          if calendar else None)
            next_row = self.conn.execute(
                """
                UPDATE reminders
                SET status='pending',
                    due_at=CASE WHEN %s::timestamptz IS NOT NULL THEN %s::timestamptz
                                ELSE GREATEST(due_at, CURRENT_TIMESTAMP) + (%s * INTERVAL '1 second') END,
                    payload=%s::jsonb,
                    last_fired_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP,
                    last_error=NULL
                WHERE id=%s
                RETURNING due_at
                """,
                (next_value, next_value, interval_seconds, json.dumps(payload, ensure_ascii=False), reminder.id),
            ).fetchone()
            next_due = next_row[0] if next_row else reminder.due_at

        self.conn.commit()
        return ReminderRecord(
            reminder.id,
            reminder.scope_type,
            reminder.scope_id,
            next_due,
            next_status,
            payload,
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
