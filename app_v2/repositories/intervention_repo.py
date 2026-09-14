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
                selected_memory_ids, generated_text
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s)
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
