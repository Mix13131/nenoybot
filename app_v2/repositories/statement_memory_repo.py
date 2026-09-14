from __future__ import annotations

from typing import Any

from app_v2.domain.enums import ScopeType
from app_v2.domain.memory import MemoryCard


_RELEVANT_TYPES = ("commitment", "decision", "quote", "contradiction", "observation")


class StatementMemoryRepository:
    """Read candidate/active group statements for grounded proactive callbacks.

    Ordinary retrieval intentionally exposes only active memories. Statement
    watching is narrower: a person's own concrete quote/commitment may be useful
    before it has enough repeated evidence to become general LONG memory.
    """

    def __init__(self, memory_repo: Any) -> None:
        self.memory_repo = memory_repo
        self.conn = memory_repo.conn

    def candidates_for_actor(
        self,
        *,
        scope_id: str,
        actor_user_id: str,
        callback_fatigue_minutes: int = 180,
        min_confidence: float = 0.60,
        limit: int = 8,
    ) -> list[MemoryCard]:
        actor_key = f"user:{actor_user_id}"
        rows = self.conn.execute(
            """
            SELECT id
            FROM memory_cards
            WHERE scope_type='group'
              AND scope_id=%s
              AND status IN ('candidate','active')
              AND memory_type = ANY(%s::text[])
              AND confidence >= %s
              AND freshness >= 0.10
              AND subject_keys && %s::text[]
              AND (
                    COALESCE((usage_policy ->> 'proactive')::boolean, FALSE) IS TRUE
                 OR COALESCE((usage_policy ->> 'callback')::boolean, FALSE) IS TRUE
              )
              AND (valid_from IS NULL OR valid_from <= CURRENT_TIMESTAMP)
              AND (valid_until IS NULL OR valid_until >= CURRENT_TIMESTAMP)
              AND (
                    last_used_at IS NULL
                 OR last_used_at <= CURRENT_TIMESTAMP - (%s * INTERVAL '1 minute')
              )
            ORDER BY pinned DESC,
                     (importance * confidence * freshness) DESC,
                     updated_at DESC
            LIMIT %s
            """,
            (
                scope_id,
                list(_RELEVANT_TYPES),
                float(min_confidence),
                [actor_key],
                max(0, int(callback_fatigue_minutes)),
                max(1, min(int(limit), 8)),
            ),
        ).fetchall()

        cards: list[MemoryCard] = []
        for row in rows:
            card = self.memory_repo.get(ScopeType.GROUP, scope_id, str(row[0]))
            if card is not None:
                cards.append(card)
        return cards

    def mark_used(self, *, scope_id: str, memory_id: str) -> bool:
        return self.memory_repo.mark_used(ScopeType.GROUP, scope_id, memory_id)
