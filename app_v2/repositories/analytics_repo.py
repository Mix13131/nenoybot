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
            f"""WITH ranked_reactions AS (
                    SELECT f.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY f.scope_id, f.intervention_id, f.user_id
                               ORDER BY f.created_at DESC, f.id DESC
                           ) AS rn
                    FROM feedback_events f
                    WHERE f.feedback_type LIKE 'reaction_%%'
                      AND f.intervention_id IS NOT NULL
                      AND f.user_id IS NOT NULL
                      AND f.created_at < %s
                      {feedback_scope}
                )
                SELECT COUNT(*)
                FROM ranked_reactions r
                JOIN interventions i ON i.id=r.intervention_id
                WHERE r.rn=1
                  AND r.feedback_type <> 'reaction_removed'
                  AND i.scope_type='group'
                  AND i.primary_action='reply'
                  AND i.created_at >= %s AND i.created_at < %s
                {intervention_scope}""",
            [end, *feedback_params, start, end, *intervention_params],
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
            f"""WITH ranked_reactions AS (
                    SELECT f.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY f.scope_id, f.intervention_id, f.user_id
                               ORDER BY f.created_at DESC, f.id DESC
                           ) AS rn
                    FROM feedback_events f
                    WHERE f.feedback_type LIKE 'reaction_%%'
                      AND f.intervention_id IS NOT NULL
                      AND f.user_id IS NOT NULL
                      AND f.created_at < %s
                      {feedback_scope}
                ),
                positive_interventions AS (
                    SELECT f.intervention_id
                    FROM feedback_events f
                    JOIN interventions i ON i.id=f.intervention_id
                    WHERE i.scope_type='group'
                      AND i.mode='group_roast'
                      AND f.feedback_type IN ('positive','like','helpful')
                      AND f.created_at >= %s AND f.created_at < %s
                      {feedback_scope}
                    UNION
                    SELECT r.intervention_id
                    FROM ranked_reactions r
                    JOIN interventions i ON i.id=r.intervention_id
                    WHERE r.rn=1
                      AND r.feedback_type='reaction_positive'
                      AND i.scope_type='group'
                      AND i.mode='group_roast'
                      AND i.created_at >= %s AND i.created_at < %s
                      {intervention_scope}
                )
                SELECT COUNT(DISTINCT intervention_id)
                FROM positive_interventions""",
            [
                end,
                *feedback_params,
                start,
                end,
                *feedback_params,
                start,
                end,
                *intervention_params,
            ],
        )
        ignored = self._scalar(
            f"""SELECT COUNT(*) FROM feedback_events f
                WHERE f.feedback_type IN ('ignored','no_engagement')
                  AND f.created_at >= %s AND f.created_at < %s
                {feedback_scope}""",
            [start, end, *feedback_params],
        )
        negative = self._scalar(
            f"""WITH ranked_reactions AS (
                    SELECT f.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY f.scope_id, f.intervention_id, f.user_id
                               ORDER BY f.created_at DESC, f.id DESC
                           ) AS rn
                    FROM feedback_events f
                    WHERE f.feedback_type LIKE 'reaction_%%'
                      AND f.intervention_id IS NOT NULL
                      AND f.user_id IS NOT NULL
                      AND f.created_at < %s
                      {feedback_scope}
                )
                SELECT
                    (
                        SELECT COUNT(*)
                        FROM feedback_events f
                        WHERE f.feedback_type IN (
                            'negative','dislike','explicit_negative',
                            'mute','shut_up','report'
                        )
                          AND f.created_at >= %s AND f.created_at < %s
                          {feedback_scope}
                    )
                    +
                    (
                        SELECT COUNT(*)
                        FROM ranked_reactions r
                        WHERE r.rn=1
                          AND r.feedback_type='reaction_negative'
                          AND r.created_at >= %s AND r.created_at < %s
                    )""",
            [
                end,
                *feedback_params,
                start,
                end,
                *feedback_params,
                start,
                end,
            ],
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

    def reaction_quality_by_mode(
        self,
        start: datetime,
        end: datetime,
        scope_id: str | None = None,
    ) -> list[dict[str, int | float | str]]:
        intervention_scope, intervention_params = _scope_filter("i", scope_id)
        rows = self.conn.execute(
            f"""WITH replies AS (
                    SELECT
                        i.id,
                        COALESCE(NULLIF(i.mode,''), 'unknown') AS mode
                    FROM interventions i
                    WHERE i.scope_type='group'
                      AND i.primary_action='reply'
                      AND i.created_at >= %s AND i.created_at < %s
                      {intervention_scope}
                ),
                ranked_reactions AS (
                    SELECT
                        f.id,
                        f.intervention_id,
                        f.user_id,
                        f.feedback_type,
                        f.payload,
                        f.created_at,
                        ROW_NUMBER() OVER (
                            PARTITION BY f.scope_id, f.intervention_id, f.user_id
                            ORDER BY f.created_at DESC, f.id DESC
                        ) AS rn
                    FROM feedback_events f
                    JOIN replies r ON r.id=f.intervention_id
                    WHERE f.feedback_type LIKE 'reaction_%%'
                      AND f.user_id IS NOT NULL
                      AND f.created_at < %s
                ),
                current_states AS (
                    SELECT *
                    FROM ranked_reactions
                    WHERE rn=1
                      AND feedback_type <> 'reaction_removed'
                )
                SELECT
                    r.mode,
                    COUNT(DISTINCT r.id) AS interventions,
                    COUNT(c.id) AS reacting_states,
                    COUNT(DISTINCT c.user_id) AS reacting_participants,
                    COUNT(DISTINCT CASE WHEN c.id IS NOT NULL THEN r.id END) AS reacted_interventions,
                    COUNT(*) FILTER (WHERE c.feedback_type='reaction_positive') AS positive_votes,
                    COUNT(*) FILTER (WHERE c.feedback_type='reaction_negative') AS negative_votes,
                    COUNT(DISTINCT CASE WHEN c.feedback_type='reaction_positive' THEN r.id END)
                        AS interventions_with_positive,
                    COUNT(DISTINCT CASE WHEN c.feedback_type='reaction_negative' THEN r.id END)
                        AS interventions_with_negative,
                    COUNT(*) FILTER (
                        WHERE (c.payload -> 'reaction_families') ? 'laughter_entertainment'
                    ) AS laughter_entertainment_states,
                    COUNT(*) FILTER (
                        WHERE (c.payload -> 'reaction_families') ? 'approval_support'
                           OR (c.payload -> 'reaction_families') ? 'affection'
                    ) AS approval_affection_states,
                    COUNT(*) FILTER (
                        WHERE (c.payload -> 'reaction_families') ? 'explicit_negative'
                    ) AS explicit_negative_states,
                    COUNT(*) FILTER (
                        WHERE (c.payload -> 'reaction_families') ? 'curiosity_surprise'
                           OR (c.payload -> 'reaction_families') ? 'sadness_empathy'
                           OR (c.payload -> 'reaction_families') ? 'ambiguous_playful'
                           OR (c.payload -> 'reaction_families') ? 'custom_unknown'
                    ) AS contextual_unknown_states
                FROM replies r
                LEFT JOIN current_states c ON c.intervention_id=r.id
                GROUP BY r.mode
                ORDER BY r.mode""",
            (start, end, *intervention_params, end),
        ).fetchall()

        result: list[dict[str, int | float | str]] = []
        for row in rows:
            interventions = int(row[1] or 0)
            reacting_states = int(row[2] or 0)
            reacting_participants = int(row[3] or 0)
            reacted_interventions = int(row[4] or 0)
            positive_votes = int(row[5] or 0)
            negative_votes = int(row[6] or 0)
            quality_votes = positive_votes + negative_votes
            result.append(
                {
                    "mode": str(row[0]),
                    "interventions": interventions,
                    "reacting_states": reacting_states,
                    "reacting_participants": reacting_participants,
                    "reacted_interventions": reacted_interventions,
                    "positive_votes": positive_votes,
                    "negative_votes": negative_votes,
                    "interventions_with_positive": int(row[7] or 0),
                    "interventions_with_negative": int(row[8] or 0),
                    "reacted_intervention_rate": (
                        reacted_interventions / interventions if interventions else 0.0
                    ),
                    "positive_share": (
                        positive_votes / quality_votes if quality_votes else 0.0
                    ),
                    "laughter_entertainment_states": int(row[9] or 0),
                    "approval_affection_states": int(row[10] or 0),
                    "explicit_negative_states": int(row[11] or 0),
                    "contextual_unknown_states": int(row[12] or 0),
                }
            )
        return result

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
