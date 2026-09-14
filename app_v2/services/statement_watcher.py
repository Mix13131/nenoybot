from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from app_v2.domain.events import EventEnvelope
from app_v2.domain.memory import MemoryCard
from app_v2.services.model_router import ModelRole


_RELEVANT_TYPES = {"commitment", "decision", "quote", "contradiction", "observation"}
_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "candidate_index": {"type": "integer", "minimum": -1, "maximum": 7},
        "relation": {
            "type": "string",
            "enum": [
                "none",
                "callback",
                "contradiction",
                "broken_commitment",
                "fulfilled_commitment",
            ],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "roast_fit": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["candidate_index", "relation", "confidence", "roast_fit"],
}


@dataclass(frozen=True)
class StatementWatchResult:
    relation: str = "none"
    confidence: float = 0.0
    roast_fit: float = 0.0
    memory_id: str | None = None
    memory_summary: str | None = None
    evidence_excerpt: str | None = None

    @property
    def strong_mismatch(self) -> bool:
        return self.relation in {"contradiction", "broken_commitment"} and self.confidence >= 0.80

    def as_action_state(self) -> dict[str, Any]:
        return {
            "relation": self.relation,
            "confidence": self.confidence,
            "roast_fit": self.roast_fit,
            "memory_id": self.memory_id,
            "memory_summary": self.memory_summary,
            "evidence_excerpt": self.evidence_excerpt,
        }


class StatementWatcher:
    """Compare a new group message with grounded statements from the same author.

    The watcher never invents history: it may only select from Memory Cards that
    already exist in the same group scope and carry message evidence.
    """

    def __init__(
        self,
        adapter: Any,
        *,
        prompt_path: Path | None = None,
        min_confidence: float = 0.72,
    ) -> None:
        self.adapter = adapter
        self.prompt_path = prompt_path or Path(__file__).resolve().parents[1] / "prompts" / "statement_watcher.md"
        self.min_confidence = min(1.0, max(0.0, min_confidence))

    def evaluate(
        self,
        *,
        event: EventEnvelope,
        candidates: Iterable[MemoryCard],
    ) -> StatementWatchResult:
        text = (event.text or "").strip()
        if not text:
            return StatementWatchResult()

        usable = [
            card
            for card in candidates
            if card.memory_type in _RELEVANT_TYPES
            and card.scope_id == event.scope_id
            and card.evidence
        ][:8]
        if not usable:
            return StatementWatchResult()

        payload = {
            "current": {
                "text": text,
                "actor_user_id": event.actor_user_id,
                "occurred_at": event.occurred_at.isoformat(),
            },
            "candidates": [
                {
                    "index": index,
                    "memory_id": card.id,
                    "type": card.memory_type,
                    "summary": card.summary,
                    "payload": card.payload,
                    "evidence": [
                        {
                            "excerpt": evidence.excerpt,
                            "timestamp": evidence.timestamp.isoformat(),
                            "author_id": evidence.author_id,
                        }
                        for evidence in card.evidence[:2]
                    ],
                }
                for index, card in enumerate(usable)
            ],
        }

        try:
            result = self.adapter.generate_json(
                ModelRole.CLASSIFIER,
                json.dumps(payload, ensure_ascii=False),
                schema_name="statement_watch",
                schema=_SCHEMA,
                instructions=self.prompt_path.read_text(encoding="utf-8"),
                event_id=event.event_id,
                max_output_tokens=220,
            )
            parsed = dict(result.parsed or {})
        except Exception:
            return StatementWatchResult()

        relation = str(parsed.get("relation") or "none")
        confidence = float(parsed.get("confidence") or 0.0)
        roast_fit = float(parsed.get("roast_fit") or 0.0)
        index = int(parsed.get("candidate_index", -1))
        if relation == "none" or confidence < self.min_confidence or not (0 <= index < len(usable)):
            return StatementWatchResult()

        card = usable[index]
        evidence_excerpt = card.evidence[0].excerpt if card.evidence else None
        return StatementWatchResult(
            relation=relation,
            confidence=confidence,
            roast_fit=roast_fit,
            memory_id=card.id,
            memory_summary=card.summary,
            evidence_excerpt=evidence_excerpt,
        )
