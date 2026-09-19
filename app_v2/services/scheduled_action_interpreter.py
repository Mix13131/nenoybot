from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app_v2.domain.enums import EventType
from app_v2.domain.events import EventEnvelope
from app_v2.services.model_router import ModelRole


PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "scheduled_action_interpreter.md"

SCHEDULED_ACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_scheduled_action": {"type": "boolean"},
        "execution_kind": {
            "type": "string",
            "enum": ["reminder", "generate_text", "external_data", "none"],
        },
        "instruction": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "is_scheduled_action",
        "execution_kind",
        "instruction",
        "confidence",
    ],
    "additionalProperties": False,
}

# This gate is intentionally about TIME, not action verbs. It only decides
# whether an explicit message is worth semantic interpretation.
_TEMPORAL_CANDIDATE_RE = re.compile(
    r"(?:"
    r"\bв\s+течение\b"
    r"|\bкажд\w*\b"
    r"|\bраз\s+в\b"
    r"|\bчерез\s+(?:\d{1,3}|минут\w*|час\w*)"
    r"|\b(?:сегодня|завтра)\b"
    r"|\bпо\s+будням\b"
    r"|\bпо\s+(?:утрам|вечерам|ночам)\b"
    r"|\bв\s+(?:[01]?\d|2[0-3])(?::[0-5]\d)?\b"
    r"|\bдо\s+(?:полуночи|утра|вечера|ночи|\d{1,2}(?::[0-5]\d)?)\b"
    r")",
    flags=re.IGNORECASE,
)

_ALLOWED_EVENTS = {
    EventType.DIRECT_MENTION,
    EventType.REPLY_TO_BOT,
    EventType.REPLY_TO_BOT_MESSAGE,
    EventType.COMMAND,
}


@dataclass(frozen=True)
class ScheduledActionInterpretation:
    is_scheduled_action: bool
    execution_kind: str
    instruction: str
    confidence: float

    def as_action_state(self) -> dict[str, Any]:
        return {
            "is_scheduled_action": self.is_scheduled_action,
            "execution_kind": self.execution_kind,
            "instruction": self.instruction,
            "confidence": self.confidence,
        }


class ScheduledActionInterpreter:
    """Semantic layer above the deterministic scheduler.

    The model decides WHAT the user wants НеНой to do over time.
    Deterministic code remains authoritative for WHEN, policy limits,
    timezone, persistence, dedupe and cancellation.
    """

    def __init__(
        self,
        adapter: Any,
        *,
        instructions: str | None = None,
        min_confidence: float = 0.72,
    ) -> None:
        self.adapter = adapter
        self.instructions = (
            instructions
            if instructions is not None
            else PROMPT_PATH.read_text(encoding="utf-8")
        )
        self.min_confidence = min_confidence

    @staticmethod
    def is_candidate(event: EventEnvelope) -> bool:
        if event.event_type not in _ALLOWED_EVENTS:
            return False
        text = (event.text or "").strip()
        return bool(text and _TEMPORAL_CANDIDATE_RE.search(text))

    def interpret(self, event: EventEnvelope) -> ScheduledActionInterpretation | None:
        if not self.is_candidate(event):
            return None

        input_text = (
            "CURRENT EVENT\n"
            f"type: {event.event_type.value}\n"
            f"text: {(event.text or '').strip()}\n"
        )
        reply_text = str(event.metadata.get("reply_to_text") or "").strip()
        if reply_text:
            input_text += f"reply_to_text: {reply_text}\n"

        try:
            result = self.adapter.generate_json(
                ModelRole.CLASSIFIER,
                input_text,
                schema_name="scheduled_action_interpretation",
                schema=SCHEDULED_ACTION_SCHEMA,
                instructions=self.instructions,
                event_id=event.event_id,
                max_output_tokens=300,
            )
            parsed = dict(result.parsed or {})
        except Exception:
            return None

        is_action = bool(parsed.get("is_scheduled_action"))
        execution_kind = str(parsed.get("execution_kind") or "none")
        instruction = " ".join(str(parsed.get("instruction") or "").split())[:700]
        try:
            confidence = float(parsed.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0

        if not is_action or execution_kind == "none":
            return None
        if confidence < self.min_confidence:
            return None
        if not instruction:
            return None
        if execution_kind not in {"reminder", "generate_text", "external_data"}:
            return None

        return ScheduledActionInterpretation(
            is_scheduled_action=True,
            execution_kind=execution_kind,
            instruction=instruction,
            confidence=max(0.0, min(1.0, confidence)),
        )
