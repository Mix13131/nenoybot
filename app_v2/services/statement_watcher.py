from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from app_v2.domain.events import EventEnvelope, SceneAnalysis
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
    evidence_message_id: str | None = None

    @property
    def strong_mismatch(self) -> bool:
        return (
            self.relation in {"contradiction", "broken_commitment"}
            and self.confidence >= 0.82
        )

    @property
    def should_intervene(self) -> bool:
        if self.strong_mismatch:
            return True
        if self.relation == "callback":
            return self.confidence >= 0.92 and self.roast_fit >= 0.78
        if self.relation == "fulfilled_commitment":
            return self.confidence >= 0.95 and self.roast_fit >= 0.85
        return False

    def as_action_state(self) -> dict[str, Any]:
        return {
            "relation": self.relation,
            "confidence": round(self.confidence, 4),
            "roast_fit": round(self.roast_fit, 4),
            "memory_id": self.memory_id,
            "memory_summary": self.memory_summary,
            "evidence_excerpt": self.evidence_excerpt,
            "evidence_message_id": self.evidence_message_id,
            "grounded": bool(self.memory_id and self.evidence_excerpt),
        }


class StatementWatcher:
    """Compare a new group message with grounded statements from the same author.

    It never invents history: it may only select an existing Memory Card from
    the same group and actor, with source-message evidence. It does not generate
    the final joke; Dispatcher/Generator decide that later.
    """

    def __init__(
        self,
        adapter: Any,
        *,
        prompt_path: Path | None = None,
    ) -> None:
        self.adapter = adapter
        self.prompt_path = (
            prompt_path
            or Path(__file__).resolve().parents[1] / "prompts" / "statement_watcher.md"
        )

    def evaluate(
        self,
        *,
        event: EventEnvelope,
        scene: SceneAnalysis,
        candidates: Iterable[MemoryCard],
    ) -> StatementWatchResult:
        text = (event.text or "").strip()
        actor = (event.actor_user_id or "").strip()
        if not text or not actor:
            return StatementWatchResult()

        if (
            scene.seriousness_score >= 0.75
            or scene.conflict_score >= 0.75
            or scene.sensitivity_score >= 0.75
        ):
            return StatementWatchResult()

        actor_key = f"user:{actor}"
        usable: list[tuple[MemoryCard, list[Any]]] = []
        for card in candidates:
            if card.memory_type not in _RELEVANT_TYPES or card.scope_id != event.scope_id:
                continue
            if actor_key not in card.subject_keys:
                continue
            own_evidence = [item for item in card.evidence if item.author_id == actor]
            if not own_evidence:
                continue
            usable.append((card, own_evidence))
            if len(usable) >= 8:
                break

        if not usable:
            return StatementWatchResult()

        payload = {
            "current": {
                "text": text,
                "actor_user_id": actor,
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
                        for evidence in own_evidence[:2]
                    ],
                }
                for index, (card, own_evidence) in enumerate(usable)
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
        if relation == "none" or confidence < 0.72 or not (0 <= index < len(usable)):
            return StatementWatchResult()

        card, own_evidence = usable[index]
        evidence = own_evidence[0]
        return StatementWatchResult(
            relation=relation,
            confidence=confidence,
            roast_fit=roast_fit,
            memory_id=card.id,
            memory_summary=card.summary,
            evidence_excerpt=evidence.excerpt,
            evidence_message_id=evidence.message_id,
        )
