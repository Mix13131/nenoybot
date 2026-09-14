from __future__ import annotations

from collections.abc import Iterable

from app_v2.domain.enums import ScopeType
from app_v2.repositories.memory_repo import MemoryRepository, RankedMemory, UsageKind


class RetrievalEngine:
    def __init__(self, repo: MemoryRepository) -> None:
        self.repo = repo

    def retrieve(
        self,
        scope_type: ScopeType,
        scope_id: str,
        *,
        usage: UsageKind = "assist",
        subject_keys: Iterable[str] = (),
        min_confidence: float = 0.5,
        min_freshness: float = 0.1,
        callback_fatigue_minutes: int = 60,
        limit: int = 8,
        expand_relations: bool = True,
    ) -> list[RankedMemory]:
        if not scope_id.strip():
            raise ValueError("scope_id must not be empty")
        if limit <= 0:
            return []

        fatigue = callback_fatigue_minutes if usage == "callback" else None
        direct = self.repo.retrieve_candidates(
            scope_type,
            scope_id,
            usage=usage,
            subject_keys=subject_keys,
            min_confidence=min_confidence,
            min_freshness=min_freshness,
            callback_fatigue_minutes=fatigue,
            limit=max(limit * 3, limit),
        )

        by_id: dict[str, RankedMemory] = {item.card.id: item for item in direct}

        if expand_relations and direct and len(by_id) < max(limit * 3, limit):
            related = self.repo.related_cards(
                scope_type,
                scope_id,
                [item.card.id for item in direct[: min(5, len(direct))]],
                usage=usage,
                min_confidence=min_confidence,
                min_freshness=min_freshness,
                callback_fatigue_minutes=fatigue,
                limit=max(limit * 2, limit),
            )
            for item in related:
                existing = by_id.get(item.card.id)
                if existing is None or item.score > existing.score:
                    by_id[item.card.id] = item

        ranked = sorted(
            by_id.values(),
            key=lambda item: (item.card.pinned, item.score, item.card.updated_at),
            reverse=True,
        )
        return ranked[:limit]
