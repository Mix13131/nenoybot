from __future__ import annotations

from datetime import datetime
from typing import Any

from app_v2.services.group_style import StyleEvidence


_REACTION_ACTOR_SQL = """
COALESCE(
    'user:' || f.user_id::text,
    NULLIF(f.payload ->> 'reactor_key', '')
)
""".strip()

_REACTION_ORDER_SQL = """
CASE
    WHEN (f.payload ->> 'source_event_id') ~ '^tg:[0-9]+$'
    THEN split_part(f.payload ->> 'source_event_id', ':', 2)::bigint
    ELSE NULL
END DESC NULLS LAST,
f.created_at DESC,
f.id DESC
""".strip()

_DIRECT_REASONS = {"direct_mention", "reply_to_bot", "question_to_bot"}
_OPERATIONAL_REASONS = {"scheduled_reminder", "birthday"}


def _reason_set(raw: Any) -> set[str]:
    if isinstance(raw, list):
        return {str(item) for item in raw}
    if isinstance(raw, tuple):
        return {str(item) for item in raw}
    return set()


def classify_intervention_family(
    *,
    primary_action: str,
    mode: str | None,
    reason_codes: Any,
    metadata: Any,
) -> str:
    reasons = _reason_set(reason_codes)
    normalized_mode = str(mode or "").strip().lower()
    action = str(primary_action or "").strip().lower()
    safe_metadata = metadata if isinstance(metadata, dict) else {}

    # setMessageReaction has no bot-authored message to receive attributable
    # feedback. Learn social acknowledgements only from the text fallback that
    # explicitly records this sanitized family on a normal sent bot message.
    if action == "reply" and safe_metadata.get("feedback_family") == "social_ack":
        return "social_ack"
    if action == "reaction_only":
        return "direct"
    if reasons & _OPERATIONAL_REASONS:
        return "direct"

    # The delivered mode is stronger evidence than secondary opportunity
    # reasons accumulated while scoring the same intervention.
    if normalized_mode == "group_callback":
        return "callback"
    if normalized_mode in {"group_roast", "group_banter"}:
        return "banter_roast"
    if "callback_opportunity" in reasons:
        return "callback"
    if "roast_opportunity" in reasons:
        return "banter_roast"
    if action == "reply" and not (reasons & _DIRECT_REASONS):
        return "proactive"
    return "direct"


class GroupStyleRepository:
    """Read only, group-scoped durable evidence for bounded social-style learning."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def recent_evidence(
        self,
        scope_id: str,
        *,
        since: datetime,
        until: datetime,
    ) -> list[StyleEvidence]:
        reaction_rows = self.conn.execute(
            f"""
            WITH ranked AS (
                SELECT
                    f.id,
                    f.intervention_id,
                    {_REACTION_ACTOR_SQL} AS actor_key,
                    f.feedback_type,
                    f.created_at,
                    i.primary_action,
                    i.mode,
                    i.reason_codes,
                    i.metadata,
                    ROW_NUMBER() OVER (
                        PARTITION BY f.scope_id, f.intervention_id, {_REACTION_ACTOR_SQL}
                        ORDER BY {_REACTION_ORDER_SQL}
                    ) AS rn
                FROM feedback_events f
                JOIN interventions i ON i.id=f.intervention_id
                WHERE f.scope_id=%s
                  AND i.scope_type='group'
                  AND i.scope_id=%s
                  AND f.feedback_type LIKE 'reaction_%%'
                  AND {_REACTION_ACTOR_SQL} IS NOT NULL
                  AND f.created_at < %s
                  AND EXISTS (
                      SELECT 1
                      FROM outbox o
                      WHERE o.channel='telegram'
                        AND o.status='sent'
                        AND o.payload #>> '{{metadata,intervention_id}}' = i.id::text
                  )
            )
            SELECT intervention_id, actor_key, feedback_type, created_at,
                   primary_action, mode, reason_codes, metadata
            FROM ranked
            WHERE rn=1
              AND created_at >= %s
              AND feedback_type IN ('reaction_positive', 'reaction_negative')
            """,
            (str(scope_id), str(scope_id), until, since),
        ).fetchall()

        text_rows = self.conn.execute(
            """
            SELECT
                f.intervention_id,
                'user:' || f.user_id::text AS actor_key,
                f.feedback_type,
                f.created_at,
                i.primary_action,
                i.mode,
                i.reason_codes,
                i.metadata
            FROM feedback_events f
            JOIN interventions i ON i.id=f.intervention_id
            WHERE f.scope_id=%s
              AND i.scope_type='group'
              AND i.scope_id=%s
              AND f.user_id IS NOT NULL
              AND f.feedback_type IN ('explicit_negative', 'mute')
              AND f.created_at >= %s
              AND f.created_at < %s
              AND EXISTS (
                  SELECT 1
                  FROM outbox o
                  WHERE o.channel='telegram'
                    AND o.status='sent'
                    AND o.payload #>> '{metadata,intervention_id}' = i.id::text
              )
            """,
            (str(scope_id), str(scope_id), since, until),
        ).fetchall()

        result: list[StyleEvidence] = []
        for row in [*reaction_rows, *text_rows]:
            feedback_type = str(row[2])
            valence = (
                "positive"
                if feedback_type == "reaction_positive"
                else "negative"
                if feedback_type in {"reaction_negative", "explicit_negative", "mute"}
                else "unknown"
            )
            if valence == "unknown":
                continue
            family = classify_intervention_family(
                primary_action=str(row[4] or ""),
                mode=str(row[5]) if row[5] is not None else None,
                reason_codes=row[6],
                metadata=row[7],
            )
            result.append(
                StyleEvidence(
                    intervention_id=int(row[0]),
                    family=family,
                    valence=valence,
                    actor_key=str(row[1]),
                    occurred_at=row[3],
                    feedback_type=feedback_type,
                )
            )
        return result
