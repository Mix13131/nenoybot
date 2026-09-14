from __future__ import annotations

from datetime import datetime
from decimal import Decimal


def _scope_filter(alias: str, scope_id: str | None) -> tuple[str, list[object]]:
    if scope_id is None:
        return "", []
    return f" AND {alias}.scope_id=%s", [scope_id]


class AnalyticsRepository:
    """Read-only PostgreSQL product analytics for НеНой v2."""

    def __init__(self, conn) -> None:
        self.conn = conn

    def personal_counts(self, start: datetime, end: datetime, scope_id: str | None = None) -> dict[str, int | float]:
        event_scope, event_params = _scope_filter("e", scope_id)
        intervention_scope, intervention_params = _scope_filter("i", scope_id)
        memory_scope, memory_params = _scope_filter("m", scope_id)

        messages = self._scalar(
            f"""SELECT COUNT(*) FROM events e
                WHERE e.scope_type='personal' AND e.created_at >= %s AND e.created_at < %s
                {event_scope}""",
            [start, end, *event_params],
        )
        replies = self._scalar(
            f"""SELECT COUNT(*) FROM interventions i
                WHERE i.scope_type='personal' AND i.primary_action='reply'
                  AND i.created_at >= %s AND i.created_at < %s
                {intervention_scope}""",
            [start, end, *intervention_params],
        )
        proactive = self._scalar(
            f"""SELECT COUNT(*)
                FROM interventions i
                JOIN events e ON e.event_id=i.event_id
                WHERE i.scope_type='personal' AND i.primary_action='reply'
                  AND i.created_at >= %s AND i.created_at < %s
                  AND e.event_type IN ('reminder_due','commitment_due','followup_due','scheduled_support_message')
                {intervention_scope}""",
            [start, end, *intervention_params],
        )
        memory_writes = self._scalar(
            f"""SELECT COUNT(*) FROM memory_cards m
                WHERE m.scope_type='personal' AND m.created_at >= %s AND m.created_at < %s
                {memory_scope}""",
            [start, end, *memory_params],
        )
        callbacks = self._scalar(
            f"""SELECT COUNT(*) FROM interventions i
                WHERE i.scope_type='personal' AND i.primary_action='reply'
                  AND i.mode='mirror'
                  AND jsonb_array_length(i.selected_memory_ids) > 0
                  AND i.created_at >= %s AND i.created_at < %s
                {intervention_scope}""",
            [start, end, *intervention_params],
        )
        cost = self._personal_cost(start, end, scope_id)
        return {
            "messages": messages,
            "replies": replies,
            "proactive_replies": proactive,
            "memory_writes": memory_writes,
            "grounded_callbacks": callbacks,
            "llm_cost_usd": cost,
        }

    def group_counts(self, start: datetime, end: datetime, scope_id: str | None = None) -> dict[str, int | float]:
        event_scope, event_params = _scope_filter("e", scope_id)
        intervention_scope, intervention_params = _scope_filter("i", scope_id)
        feedback_scope, feedback_params = _scope_filter("f", scope_id)

        participant_where = ""
        participant_params: list[object] = []
        if scope_id is not None:
            participant_where = " AND c.telegram_chat_id=%s"
            participant_params.append(int(scope_id))
        participants = self._scalar(
            f"""SELECT COUNT(DISTINCT cm.user_id)
                FROM chat_members cm JOIN chats c ON c.id=cm.chat_id
                WHERE c.chat_type='group' {participant_where}""",
            participant_params,
        )
        organic = self._scalar(
            f"""SELECT COUNT(DISTINCT e.actor_user_id)
                FROM events e
                WHERE e.scope_type='group'
                  AND e.event_type IN ('direct_mention','reply_to_bot','reply_to_bot_message')
                  AND e.actor_user_id IS NOT NULL
                  AND e.created_at >= %s AND e.created_at < %s
                {event_scope}""",
            [start, end, *event_params],
        )
        direct_mentions = self._scalar(
            f"""SELECT COUNT(*) FROM events e
                WHERE e.scope_type='group' AND e.event_type='direct_mention'
                  AND e.created_at >= %s AND e.created_at < %s
                {event_scope}""",
            [start, end, *event_params],
        )
        unsolicited = self._scalar(
            f"""SELECT COUNT(*) FROM interventions i
                WHERE i.scope_type='group' AND i.primary_action='reply'
                  AND i.created_at >= %s AND i.created_at < %s
                  AND NOT (i.reason_codes ?| ARRAY['direct_mention','reply_to_bot','question_to_bot'])
                {intervention_scope}""",
            [start, end, *intervention_params],
        )
        replies = self._scalar(
            f"""SELECT COUNT(*) FROM interventions i
                WHERE i.scope_type='group' AND i.primary_action='reply'
                  AND i.created_at >= %s AND i.created_at < %s
                {intervention_scope}""",
            [start, end, *intervention_params],
        )
        reactions = self._scalar(
            f"""SELECT COUNT(*) FROM feedback_events f
                WHERE f.feedback_type LIKE 'reaction_%'
                  AND f.feedback_type <> 'reaction_removed'
                  AND f.created_at >= %s AND f.created_at < %s
                {feedback_scope}""",
            [start, end, *feedback_params],
        )
        roast_total = self._scalar(
            f"""SELECT COUNT(*) FROM interventions i
                WHERE i.scope_type='group' AND i.mode='group_roast'
                  AND i.primary_action='reply'
                  AND i.created_at >= %s AND i.created_at < %s
                {intervention_scope}""",
            [start, end, *intervention_params],
        )
        roast_positive = self._scalar(
            f"""SELECT COUNT(DISTINCT f.intervention_id)
                FROM feedback_events f
                JOIN interventions i ON i.id=f.intervention_id
                WHERE i.scope_type='group' AND i.mode='group_roast'
                  AND f.feedback_type IN ('reaction_positive','positive','like','helpful')
                  AND f.created_at >= %s AND f.created_at < %s
                {feedback_scope}""",
            [start, end, *feedback_params],
        )
        ignored = self._scalar(
            f"""SELECT COUNT(*) FROM feedback_events f
                WHERE f.feedback_type IN ('ignored','no_engagement')
                  AND f.created_at >= %s AND f.created_at < %s
                {feedback_scope}""",
            [start, end, *feedback_params],
        )
        negative = self._scalar(
            f"""SELECT COUNT(*) FROM feedback_events f
                WHERE f.feedback_type IN ('negative','dislike','reaction_negative','explicit_negative','mute','shut_up','report')
                  AND f.created_at >= %s AND f.created_at < %s
                {feedback_scope}""",
            [start, end, *feedback_params],
        )
        mute = self._scalar(
            f"""SELECT COUNT(*) FROM feedback_events f
                WHERE f.feedback_type IN ('mute','shut_up')
                  AND f.created_at >= %s AND f.created_at < %s
                {feedback_scope}""",
            [start, end, *feedback_params],
        )
        cost = self._group_cost(start, end, scope_id)
        return {
            "participant_count": participants,
            "organic_participants": organic,
            "direct_mentions": direct_mentions,
            "unsolicited_interventions": unsolicited,
            "replies": replies,
            "reactions": reactions,
            "reaction_rate": reactions / replies if replies else 0.0,
            "roast_interventions": roast_total,
            "roast_positive": roast_positive,
            "roast_hit_rate": roast_positive / roast_total if roast_total else 0.0,
            "ignored_interventions": ignored,
            "negative_feedback": negative,
            "mute_events": mute,
            "llm_cost_usd": cost,
        }

    def group_cost_by_day(self, start: datetime, end: datetime, scope_id: str | None = None) -> list[dict[str, object]]:
        scope_sql = ""
        params: list[object] = [start, end]
        if scope_id is not None:
            scope_sql = " AND e.scope_id=%s"
            params.append(scope_id)
        rows = self.conn.execute(
            f"""SELECT date_trunc('day', u.created_at) AS day,
                       e.scope_id,
                       COALESCE(SUM(u.estimated_cost_usd),0) AS cost
                FROM llm_usage u
                JOIN events e ON e.event_id=u.event_id
                WHERE e.scope_type='group'
                  AND u.created_at >= %s AND u.created_at < %s
                  {scope_sql}
                GROUP BY day, e.scope_id
                ORDER BY day, e.scope_id""",
            tuple(params),
        ).fetchall()
        return [
            {"day": row[0], "scope_id": str(row[1]), "llm_cost_usd": float(row[2] or 0)}
            for row in rows
        ]

    def _personal_cost(self, start: datetime, end: datetime, scope_id: str | None) -> float:
        return self._scope_cost("personal", start, end, scope_id)

    def _group_cost(self, start: datetime, end: datetime, scope_id: str | None) -> float:
        return self._scope_cost("group", start, end, scope_id)

    def _scope_cost(self, scope_type: str, start: datetime, end: datetime, scope_id: str | None) -> float:
        scope_sql = ""
        params: list[object] = [scope_type, start, end]
        if scope_id is not None:
            scope_sql = " AND e.scope_id=%s"
            params.append(scope_id)
        row = self.conn.execute(
            f"""SELECT COALESCE(SUM(u.estimated_cost_usd),0)
                FROM llm_usage u JOIN events e ON e.event_id=u.event_id
                WHERE e.scope_type=%s AND u.created_at >= %s AND u.created_at < %s
                {scope_sql}""",
            tuple(params),
        ).fetchone()
        return float(row[0] or 0) if row else 0.0

    def _scalar(self, sql: str, params: list[object]) -> int:
        row = self.conn.execute(sql, tuple(params)).fetchone()
        value = row[0] if row else 0
        if isinstance(value, Decimal):
            return int(value)
        return int(value or 0)
