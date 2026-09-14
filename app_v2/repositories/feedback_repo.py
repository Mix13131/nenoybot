from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from app_v2.domain.feedback import FeedbackEvent


@dataclass(frozen=True)
class FeedbackRecord:
    id: int
    feedback_id: str
    intervention_id: int | None
    feedback_type: str
    created: bool


class FeedbackRepository:
    def __init__(self, conn) -> None:
        self.conn = conn

    def find_intervention_by_bot_message(self, scope_id: str, telegram_message_id: str | int) -> int | None:
        try:
            message_id = int(telegram_message_id)
        except (TypeError, ValueError):
            return None
        row = self.conn.execute(
            """
            SELECT NULLIF(o.payload -> 'metadata' ->> 'intervention_id', '')::bigint
            FROM outbox o
            WHERE o.channel='telegram'
              AND o.destination_id=%s
              AND o.telegram_message_id=%s
              AND o.status='sent'
            ORDER BY o.sent_at DESC NULLS LAST, o.id DESC
            LIMIT 1
            """,
            (scope_id, message_id),
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def latest_intervention(
        self,
        scope_id: str,
        *,
        before: datetime,
        max_age_hours: int = 24,
    ) -> int | None:
        row = self.conn.execute(
            """
            SELECT id
            FROM interventions
            WHERE scope_id=%s
              AND primary_action='reply'
              AND created_at <= %s
              AND created_at >= %s
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (scope_id, before, before - timedelta(hours=max_age_hours)),
        ).fetchone()
        return int(row[0]) if row else None

    def record(self, feedback: FeedbackEvent, *, intervention_id: int | None = None) -> FeedbackRecord:
        numeric_value: float | None
        if isinstance(feedback.value, bool):
            numeric_value = 1.0 if feedback.value else 0.0
        elif isinstance(feedback.value, (int, float)):
            numeric_value = float(feedback.value)
        else:
            numeric_value = None

        row = self.conn.execute(
            """
            INSERT INTO feedback_events(
                feedback_id, intervention_id, scope_id, user_id,
                feedback_type, value, payload, created_at
            )
            VALUES (
                %s, %s, %s,
                (SELECT id FROM users WHERE telegram_user_id=%s LIMIT 1),
                %s, %s, %s::jsonb, %s
            )
            ON CONFLICT (feedback_id) WHERE feedback_id IS NOT NULL DO NOTHING
            RETURNING id
            """,
            (
                feedback.feedback_id,
                intervention_id,
                feedback.scope_id,
                int(feedback.user_id) if feedback.user_id is not None else None,
                feedback.feedback_type,
                numeric_value,
                json.dumps(feedback.payload, ensure_ascii=False, default=str),
                feedback.occurred_at,
            ),
        ).fetchone()
        if row is not None:
            self.conn.commit()
            return FeedbackRecord(
                id=int(row[0]),
                feedback_id=feedback.feedback_id,
                intervention_id=intervention_id,
                feedback_type=feedback.feedback_type,
                created=True,
            )

        existing = self.conn.execute(
            """
            SELECT id, intervention_id, feedback_type
            FROM feedback_events
            WHERE feedback_id=%s
            """,
            (feedback.feedback_id,),
        ).fetchone()
        self.conn.commit()
        if existing is None:
            raise RuntimeError("Feedback dedupe conflict occurred but existing row was not found")
        return FeedbackRecord(
            id=int(existing[0]),
            feedback_id=feedback.feedback_id,
            intervention_id=int(existing[1]) if existing[1] is not None else None,
            feedback_type=str(existing[2]),
            created=False,
        )
