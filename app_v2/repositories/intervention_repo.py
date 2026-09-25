from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app_v2.domain.decisions import DispatcherDecision
from app_v2.domain.enums import ScopeType


@dataclass(frozen=True)
class InterventionRecord:
    id: int
    event_id: str
    scope_type: ScopeType
    scope_id: str
    generated_text: str | None


class InterventionRepository:
    def __init__(self, conn) -> None:
        self.conn = conn

    def record(
        self,
        *,
        event_id: str,
        scope_type: ScopeType,
        scope_id: str,
        decision: DispatcherDecision,
        selected_memory_ids: list[str],
        generated_text: str | None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> InterventionRecord:
        metadata = dict(decision.metadata)
        metadata.update(extra_metadata or {})
        reason_codes = [reason.value for reason in decision.reason_codes]
        policy_version = str(metadata.get("policy_version") or "unknown")
        row = self.conn.execute(
            """
            INSERT INTO interventions(
                event_id, scope_type, scope_id, primary_action, mode,
                intervention_score, reason_codes, policy_version,
                selected_memory_ids, generated_text, metadata
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s,%s::jsonb)
            RETURNING id
            """,
            (
                event_id,
                scope_type.value,
                scope_id,
                decision.primary_action.value,
                decision.mode.value if decision.mode else None,
                decision.intervention_score,
                json.dumps(reason_codes, ensure_ascii=False),
                policy_version,
                json.dumps(selected_memory_ids, ensure_ascii=False),
                generated_text,
                json.dumps(metadata, ensure_ascii=False, default=str),
            ),
        ).fetchone()
        self.conn.commit()
        return InterventionRecord(
            id=int(row[0]),
            event_id=event_id,
            scope_type=scope_type,
            scope_id=scope_id,
            generated_text=generated_text,
        )

    def selected_memory_ids_for_bot_message(
        self,
        *,
        scope_type: ScopeType,
        scope_id: str,
        telegram_message_id: str | int | None,
    ) -> tuple[str, ...]:
        """Resolve memories behind one actually sent bot message in this scope.

        This is intentionally reply-bound. A generic "forget this" without a
        verifiable replied-to bot message must not guess which memory to erase.
        """

        try:
            message_id = int(telegram_message_id) if telegram_message_id is not None else None
        except (TypeError, ValueError):
            return ()
        if message_id is None:
            return ()

        row = self.conn.execute(
            """
            SELECT i.selected_memory_ids
            FROM outbox o
            JOIN interventions i
              ON i.id = NULLIF(o.payload -> 'metadata' ->> 'intervention_id', '')::bigint
            WHERE o.channel='telegram'
              AND o.destination_id=%s
              AND o.telegram_message_id=%s
              AND o.status='sent'
              AND i.scope_type=%s
              AND i.scope_id=%s
            ORDER BY o.sent_at DESC NULLS LAST, o.id DESC
            LIMIT 1
            """,
            (scope_id, message_id, scope_type.value, scope_id),
        ).fetchone()
        if not row:
            return ()
        raw = row[0]
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                return ()
        if not isinstance(raw, list):
            return ()
        return tuple(str(item) for item in raw if str(item).strip())
