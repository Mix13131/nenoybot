from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Literal

from app_v2.domain.enums import MemoryStatus, ScopeType
from app_v2.domain.memory import MemoryCard, MemoryRelation

UsageKind = Literal["assist", "callback", "roast", "proactive"]
_ALLOWED_USAGE: set[str] = {"assist", "callback", "roast", "proactive"}


class MemoryRepositoryError(RuntimeError):
    pass


class MemoryScopeError(MemoryRepositoryError):
    pass


@dataclass(frozen=True)
class RankedMemory:
    card: MemoryCard
    score: float


_CARD_COLUMNS = """
    id, scope_type, scope_id, memory_type, subject_keys, summary, payload,
    importance, confidence, freshness, status, origin, pinned, usage_policy,
    evidence, source_count, created_at, updated_at, last_confirmed_at,
    last_used_at, valid_from, valid_until, superseded_by
"""


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _row_to_card(row) -> MemoryCard:
    return MemoryCard(
        id=row[0],
        scope_type=row[1],
        scope_id=row[2],
        memory_type=row[3],
        subject_keys=list(row[4] or []),
        summary=row[5],
        payload=dict(row[6] or {}),
        importance=float(row[7]),
        confidence=float(row[8]),
        freshness=float(row[9]),
        status=row[10],
        origin=row[11],
        pinned=bool(row[12]),
        usage_policy=dict(row[13] or {}),
        evidence=list(row[14] or []),
        source_count=int(row[15]),
        created_at=row[16],
        updated_at=row[17],
        last_confirmed_at=row[18],
        last_used_at=row[19],
        valid_from=row[20],
        valid_until=row[21],
        superseded_by=row[22],
    )


def _usage_clause(usage: UsageKind) -> str:
    if usage not in _ALLOWED_USAGE:
        raise ValueError(f"Unsupported memory usage kind: {usage}")
    return f"COALESCE((usage_policy ->> '{usage}')::boolean, FALSE) IS TRUE"


class MemoryRepository:
    def __init__(self, conn) -> None:
        self.conn = conn

    def create(self, card: MemoryCard, *, subject_user_id: int | None = None) -> MemoryCard:
        row = self.conn.execute(
            f"""
            INSERT INTO memory_cards(
                id, scope_type, scope_id, memory_type, subject_user_id,
                subject_keys, summary, payload, importance, confidence,
                freshness, status, origin, pinned, usage_policy, evidence,
                source_count, created_at, updated_at, last_confirmed_at,
                last_used_at, valid_from, valid_until, superseded_by
            )
            VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,
                %s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s
            )
            RETURNING {_CARD_COLUMNS}
            """,
            (
                card.id,
                card.scope_type.value,
                card.scope_id,
                card.memory_type,
                subject_user_id,
                card.subject_keys,
                card.summary,
                _json(card.payload),
                card.importance,
                card.confidence,
                card.freshness,
                card.status.value,
                card.origin.value,
                card.pinned,
                _json(card.usage_policy.model_dump()),
                _json([item.model_dump(mode="json") for item in card.evidence]),
                card.source_count,
                card.created_at,
                card.updated_at,
                card.last_confirmed_at,
                card.last_used_at,
                card.valid_from,
                card.valid_until,
                card.superseded_by,
            ),
        ).fetchone()
        self.conn.commit()
        return _row_to_card(row)

    def get(self, scope_type: ScopeType, scope_id: str, memory_id: str) -> MemoryCard | None:
        row = self.conn.execute(
            f"SELECT {_CARD_COLUMNS} FROM memory_cards WHERE id=%s AND scope_type=%s AND scope_id=%s",
            (memory_id, scope_type.value, scope_id),
        ).fetchone()
        return _row_to_card(row) if row else None

    def update(self, card: MemoryCard, *, subject_user_id: int | None = None) -> MemoryCard:
        row = self.conn.execute(
            f"""
            UPDATE memory_cards
            SET memory_type=%s,
                subject_user_id=%s,
                subject_keys=%s,
                summary=%s,
                payload=%s::jsonb,
                importance=%s,
                confidence=%s,
                freshness=%s,
                status=%s,
                origin=%s,
                pinned=%s,
                usage_policy=%s::jsonb,
                evidence=%s::jsonb,
                source_count=%s,
                updated_at=%s,
                last_confirmed_at=%s,
                last_used_at=%s,
                valid_from=%s,
                valid_until=%s,
                superseded_by=%s
            WHERE id=%s AND scope_type=%s AND scope_id=%s
            RETURNING {_CARD_COLUMNS}
            """,
            (
                card.memory_type,
                subject_user_id,
                card.subject_keys,
                card.summary,
                _json(card.payload),
                card.importance,
                card.confidence,
                card.freshness,
                card.status.value,
                card.origin.value,
                card.pinned,
                _json(card.usage_policy.model_dump()),
                _json([item.model_dump(mode="json") for item in card.evidence]),
                card.source_count,
                card.updated_at,
                card.last_confirmed_at,
                card.last_used_at,
                card.valid_from,
                card.valid_until,
                card.superseded_by,
                card.id,
                card.scope_type.value,
                card.scope_id,
            ),
        ).fetchone()
        self.conn.commit()
        if row is None:
            raise MemoryScopeError(
                f"Memory {card.id} not found inside scope {card.scope_type.value}:{card.scope_id}"
            )
        return _row_to_card(row)

    def set_status(
        self,
        scope_type: ScopeType,
        scope_id: str,
        memory_id: str,
        status: MemoryStatus,
        *,
        superseded_by: str | None = None,
    ) -> bool:
        row = self.conn.execute(
            """
            UPDATE memory_cards
            SET status=%s, superseded_by=%s, updated_at=CURRENT_TIMESTAMP
            WHERE id=%s AND scope_type=%s AND scope_id=%s
            RETURNING id
            """,
            (status.value, superseded_by, memory_id, scope_type.value, scope_id),
        ).fetchone()
        self.conn.commit()
        return row is not None

    def archive(self, scope_type: ScopeType, scope_id: str, memory_id: str) -> bool:
        return self.set_status(scope_type, scope_id, memory_id, MemoryStatus.ARCHIVED)

    def mark_used(self, scope_type: ScopeType, scope_id: str, memory_id: str) -> bool:
        row = self.conn.execute(
            """
            UPDATE memory_cards
            SET last_used_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
            WHERE id=%s AND scope_type=%s AND scope_id=%s
            RETURNING id
            """,
            (memory_id, scope_type.value, scope_id),
        ).fetchone()
        self.conn.commit()
        return row is not None

    def link(self, relation: MemoryRelation) -> bool:
        row = self.conn.execute(
            """
            INSERT INTO memory_relations(
                id, scope_type, scope_id, from_memory_id,
                relation_type, to_memory_id, weight
            )
            SELECT %s,%s,%s,%s,%s,%s,%s
            WHERE EXISTS (
                SELECT 1 FROM memory_cards
                WHERE id=%s AND scope_type=%s AND scope_id=%s
            )
              AND EXISTS (
                SELECT 1 FROM memory_cards
                WHERE id=%s AND scope_type=%s AND scope_id=%s
            )
            ON CONFLICT (from_memory_id, relation_type, to_memory_id)
            DO UPDATE SET weight=EXCLUDED.weight
            RETURNING id
            """,
            (
                relation.id,
                relation.scope_type.value,
                relation.scope_id,
                relation.from_memory_id,
                relation.relation_type,
                relation.to_memory_id,
                relation.weight,
                relation.from_memory_id,
                relation.scope_type.value,
                relation.scope_id,
                relation.to_memory_id,
                relation.scope_type.value,
                relation.scope_id,
            ),
        ).fetchone()
        self.conn.commit()
        if row is None:
            raise MemoryScopeError("Both relation endpoints must exist in the requested memory scope")
        return True

    def retrieve_candidates(
        self,
        scope_type: ScopeType,
        scope_id: str,
        *,
        usage: UsageKind = "assist",
        subject_keys: Iterable[str] = (),
        min_confidence: float = 0.5,
        min_freshness: float = 0.1,
        callback_fatigue_minutes: int | None = None,
        limit: int = 24,
    ) -> list[RankedMemory]:
        usage_sql = _usage_clause(usage)
        keys = list(subject_keys)
        conditions = [
            "scope_type=%s",
            "scope_id=%s",
            "status='active'",
            "confidence >= %s",
            "freshness >= %s",
            usage_sql,
            "(valid_from IS NULL OR valid_from <= CURRENT_TIMESTAMP)",
            "(valid_until IS NULL OR valid_until >= CURRENT_TIMESTAMP)",
        ]
        params: list[object] = [scope_type.value, scope_id, min_confidence, min_freshness]

        subject_boost_sql = "0"
        if keys:
            conditions.append("subject_keys && %s::text[]")
            params.append(keys)
            subject_boost_sql = "0.25"

        if callback_fatigue_minutes is not None:
            conditions.append(
                "(last_used_at IS NULL OR last_used_at <= CURRENT_TIMESTAMP - (%s * INTERVAL '1 minute'))"
            )
            params.append(callback_fatigue_minutes)

        params.append(max(1, limit))
        rows = self.conn.execute(
            f"""
            SELECT {_CARD_COLUMNS},
                   (importance * confidence * freshness + {subject_boost_sql}) AS rank_score
            FROM memory_cards
            WHERE {' AND '.join(conditions)}
            ORDER BY pinned DESC, rank_score DESC, updated_at DESC
            LIMIT %s
            """,
            tuple(params),
        ).fetchall()
        return [RankedMemory(_row_to_card(row[:23]), float(row[23])) for row in rows]

    def related_cards(
        self,
        scope_type: ScopeType,
        scope_id: str,
        seed_ids: Iterable[str],
        *,
        usage: UsageKind = "assist",
        min_confidence: float = 0.5,
        min_freshness: float = 0.1,
        limit: int = 16,
    ) -> list[RankedMemory]:
        seeds = list(seed_ids)
        if not seeds:
            return []
        usage_sql = _usage_clause(usage)
        rows = self.conn.execute(
            f"""
            SELECT {_CARD_COLUMNS},
                   (mc.importance * mc.confidence * mc.freshness + mr.weight * 0.15) AS rank_score
            FROM memory_relations mr
            JOIN memory_cards mc
              ON mc.id = CASE
                    WHEN mr.from_memory_id = ANY(%s::text[]) THEN mr.to_memory_id
                    ELSE mr.from_memory_id
                 END
            WHERE mr.scope_type=%s
              AND mr.scope_id=%s
              AND mc.scope_type=%s
              AND mc.scope_id=%s
              AND (mr.from_memory_id = ANY(%s::text[]) OR mr.to_memory_id = ANY(%s::text[]))
              AND mc.status='active'
              AND mc.confidence >= %s
              AND mc.freshness >= %s
              AND {usage_sql.replace('usage_policy', 'mc.usage_policy')}
              AND (mc.valid_from IS NULL OR mc.valid_from <= CURRENT_TIMESTAMP)
              AND (mc.valid_until IS NULL OR mc.valid_until >= CURRENT_TIMESTAMP)
            ORDER BY rank_score DESC, mc.updated_at DESC
            LIMIT %s
            """,
            (
                seeds,
                scope_type.value,
                scope_id,
                scope_type.value,
                scope_id,
                seeds,
                seeds,
                min_confidence,
                min_freshness,
                max(1, limit),
            ),
        ).fetchall()
        return [RankedMemory(_row_to_card(row[:23]), float(row[23])) for row in rows]
