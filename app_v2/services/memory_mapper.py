from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app_v2.domain.enums import MemoryOrigin, MemoryStatus, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.domain.memory import MemoryCard, MemoryEvidence, UsagePolicy
from app_v2.services.model_router import ModelRole


_ALLOWED_TYPES = {
    "goal", "project", "commitment", "decision", "plan", "event",
    "preference", "observation", "contradiction", "quote",
    "running_joke", "pattern",
}

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "memory_type": {"type": "string", "enum": sorted(_ALLOWED_TYPES)},
                    "semantic_key": {"type": "string", "minLength": 1},
                    "summary": {"type": "string", "minLength": 1},
                    "subject_keys": {"type": "array", "items": {"type": "string"}},
                    "payload": {"type": "object"},
                    "importance": {"type": "number", "minimum": 0, "maximum": 1},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence_count": {"type": "integer", "minimum": 1},
                    "episode_count": {"type": "integer", "minimum": 1},
                    "usage_policy": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "assist": {"type": "boolean"},
                            "callback": {"type": "boolean"},
                            "roast": {"type": "boolean"},
                            "proactive": {"type": "boolean"},
                        },
                        "required": ["assist", "callback", "roast", "proactive"],
                    },
                },
                "required": [
                    "memory_type", "semantic_key", "summary", "subject_keys",
                    "payload", "importance", "confidence", "evidence_count",
                    "episode_count", "usage_policy",
                ],
            },
        }
    },
    "required": ["candidates"],
}


@dataclass(frozen=True)
class MapperResult:
    written: tuple[MemoryCard, ...] = ()
    forgotten_ids: tuple[str, ...] = ()
    failed: bool = False
    reason: str | None = None


class MemoryMapperStore:
    """Small persistence facade for mapper-specific semantic merge operations."""

    def __init__(self, memory_repo: Any) -> None:
        self.repo = memory_repo

    def find_semantic_match(
        self,
        scope_type: ScopeType,
        scope_id: str,
        semantic_key: str,
    ) -> MemoryCard | None:
        conn = getattr(self.repo, "conn", None)
        if conn is None:
            finder = getattr(self.repo, "find_semantic_match", None)
            return finder(scope_type, scope_id, semantic_key) if finder else None

        row = conn.execute(
            """
            SELECT id
            FROM memory_cards
            WHERE scope_type=%s
              AND scope_id=%s
              AND status IN ('candidate','active')
              AND payload ->> 'semantic_key' = %s
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (scope_type.value, scope_id, semantic_key),
        ).fetchone()
        if not row:
            return None
        return self.repo.get(scope_type, scope_id, row[0])

    def create(self, card: MemoryCard) -> MemoryCard:
        return self.repo.create(card)

    def update(self, card: MemoryCard) -> MemoryCard:
        return self.repo.update(card)

    def archive(self, scope_type: ScopeType, scope_id: str, memory_id: str) -> bool:
        return self.repo.archive(scope_type, scope_id, memory_id)


class MemoryMapper:
    def __init__(
        self,
        *,
        store: Any,
        adapter: Any | None = None,
        prompt_path: Path | None = None,
    ) -> None:
        self.store = store
        self.adapter = adapter
        self.prompt_path = prompt_path or Path(__file__).resolve().parents[1] / "prompts" / "memory_mapper.md"

    def map_event(
        self,
        event: EventEnvelope,
        *,
        target_memory_ids: Iterable[str] = (),
        recent_context: str = "",
    ) -> MapperResult:
        text = (event.text or "").strip()
        if not text:
            return MapperResult(reason="no_text")

        lowered = text.lower()
        if lowered in {"забудь это", "забудь", "forget this", "forget it"}:
            forgotten: list[str] = []
            for memory_id in target_memory_ids:
                if self.store.archive(event.scope_type, event.scope_id, memory_id):
                    forgotten.append(memory_id)
            return MapperResult(forgotten_ids=tuple(forgotten), reason="explicit_forget")

        explicit = self._extract_explicit_remember(text)
        if explicit is not None:
            candidate = {
                "memory_type": "fact",
                "semantic_key": self._semantic_key("explicit", explicit),
                "summary": explicit,
                "subject_keys": self._default_subject_keys(event),
                "payload": {},
                "importance": 0.9,
                "confidence": 0.98,
                "evidence_count": 1,
                "episode_count": 1,
                "usage_policy": {
                    "assist": True,
                    "callback": True,
                    "roast": False,
                    "proactive": False,
                },
            }
            card = self._upsert_candidate(event, candidate, explicit=True)
            return MapperResult(written=(card,), reason="explicit_remember")

        if self.adapter is None:
            return MapperResult(reason="no_mapper_adapter")

        try:
            prompt = self.prompt_path.read_text(encoding="utf-8")
            input_text = json.dumps(
                {
                    "event": event.model_dump(mode="json"),
                    "recent_context": recent_context[-4000:],
                },
                ensure_ascii=False,
            )
            result = self.adapter.generate_json(
                ModelRole.MEMORY,
                input_text,
                schema_name="nenoy_memory_mapper",
                schema=_SCHEMA,
                instructions=prompt,
                event_id=event.event_id,
                max_output_tokens=1200,
            )
            parsed = result.parsed or {"candidates": []}
            written = tuple(
                self._upsert_candidate(event, candidate, explicit=False)
                for candidate in parsed.get("candidates", [])
                if candidate.get("memory_type") in _ALLOWED_TYPES
            )
            return MapperResult(written=written)
        except Exception as exc:
            return MapperResult(failed=True, reason=f"mapper_failure:{type(exc).__name__}")

    @staticmethod
    def _extract_explicit_remember(text: str) -> str | None:
        lowered = text.lower()
        for prefix in ("запомни:", "запомни ", "remember:", "remember "):
            if lowered.startswith(prefix):
                value = text[len(prefix):].strip()
                return value or None
        return None

    @staticmethod
    def _semantic_key(kind: str, text: str) -> str:
        normalized = " ".join(text.lower().split())
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
        return f"{kind}:{digest}"

    @staticmethod
    def _default_subject_keys(event: EventEnvelope) -> list[str]:
        if event.actor_user_id:
            return [f"user:{event.actor_user_id}"]
        return []

    def _evidence(self, event: EventEnvelope) -> MemoryEvidence:
        return MemoryEvidence(
            message_id=event.message_id,
            author_id=event.actor_user_id,
            timestamp=event.occurred_at,
            excerpt=(event.text or "")[:240].strip(),
        )

    def _upsert_candidate(
        self,
        event: EventEnvelope,
        candidate: dict[str, Any],
        *,
        explicit: bool,
    ) -> MemoryCard:
        now = datetime.now(timezone.utc)
        memory_type = str(candidate["memory_type"])
        evidence_count = int(candidate.get("evidence_count", 1))
        episode_count = int(candidate.get("episode_count", 1))

        if memory_type == "pattern" and (evidence_count < 3 or episode_count < 2):
            memory_type = "observation"
            status = MemoryStatus.CANDIDATE
        elif explicit:
            status = MemoryStatus.ACTIVE
        else:
            status = MemoryStatus.CANDIDATE

        semantic_key = str(candidate["semantic_key"]).strip()
        existing = self.store.find_semantic_match(event.scope_type, event.scope_id, semantic_key)
        evidence = self._evidence(event)
        usage_policy = UsagePolicy(**candidate.get("usage_policy", {}))
        payload = dict(candidate.get("payload", {}))
        payload.update(
            {
                "semantic_key": semantic_key,
                "evidence_count": evidence_count,
                "episode_count": episode_count,
            }
        )

        confidence = float(candidate.get("confidence", 0.5))
        if not explicit:
            confidence = min(confidence, 0.70)

        if existing is None:
            card = MemoryCard(
                id=f"mem_{uuid.uuid4().hex}",
                scope_type=event.scope_type,
                scope_id=event.scope_id,
                memory_type=memory_type,
                subject_keys=list(candidate.get("subject_keys") or self._default_subject_keys(event)),
                summary=str(candidate["summary"]).strip(),
                payload=payload,
                importance=float(candidate.get("importance", 0.5)),
                confidence=confidence,
                freshness=1.0,
                status=status,
                origin=MemoryOrigin.EXPLICIT if explicit else MemoryOrigin.INFERRED,
                usage_policy=usage_policy,
                evidence=[evidence],
                source_count=1,
                created_at=now,
                updated_at=now,
                last_confirmed_at=now if explicit else None,
            )
            return self.store.create(card)

        evidence_items = list(existing.evidence)
        identity = (evidence.message_id, evidence.author_id, evidence.timestamp, evidence.excerpt)
        seen = {(e.message_id, e.author_id, e.timestamp, e.excerpt) for e in evidence_items}
        if identity not in seen:
            evidence_items.append(evidence)

        merged_status = existing.status
        if explicit:
            merged_status = MemoryStatus.ACTIVE
        elif memory_type == "pattern" and evidence_count >= 3 and episode_count >= 2:
            merged_status = MemoryStatus.ACTIVE

        updated = existing.model_copy(
            update={
                "memory_type": memory_type,
                "summary": str(candidate["summary"]).strip(),
                "payload": {**existing.payload, **payload},
                "importance": max(existing.importance, float(candidate.get("importance", 0.5))),
                "confidence": max(existing.confidence, confidence),
                "freshness": 1.0,
                "status": merged_status,
                "origin": MemoryOrigin.EXPLICIT if explicit else existing.origin,
                "usage_policy": usage_policy,
                "evidence": evidence_items,
                "source_count": max(existing.source_count, len(evidence_items), evidence_count),
                "updated_at": now,
                "last_confirmed_at": now if explicit else existing.last_confirmed_at,
            }
        )
        return self.store.update(updated)
