from __future__ import annotations

from pathlib import Path
from typing import Any

from app_v2.domain.enums import EventType
from app_v2.domain.events import EventEnvelope, SceneAnalysis
from app_v2.services.model_router import ModelRole

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "scene_analyzer.md"

SCENE_ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "question_to_bot": {"type": "boolean"},
        "command_intent": {"type": ["string", "null"]},
        "banter_score": {"type": "number", "minimum": 0, "maximum": 1},
        "seriousness_score": {"type": "number", "minimum": 0, "maximum": 1},
        "conflict_score": {"type": "number", "minimum": 0, "maximum": 1},
        "sensitivity_score": {"type": "number", "minimum": 0, "maximum": 1},
        "roast_opportunity": {"type": "number", "minimum": 0, "maximum": 1},
        "callback_opportunity": {"type": "number", "minimum": 0, "maximum": 1},
        "help_opportunity": {"type": "number", "minimum": 0, "maximum": 1},
        "memory_value": {"type": "number", "minimum": 0, "maximum": 1},
        "contradiction_score": {"type": "number", "minimum": 0, "maximum": 1},
        "commitment_signal": {"type": "number", "minimum": 0, "maximum": 1},
        "decision_signal": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "question_to_bot",
        "command_intent",
        "banter_score",
        "seriousness_score",
        "conflict_score",
        "sensitivity_score",
        "roast_opportunity",
        "callback_opportunity",
        "help_opportunity",
        "memory_value",
        "contradiction_score",
        "commitment_signal",
        "decision_signal",
    ],
    "additionalProperties": False,
}


def _deterministic_direct_mention(event: EventEnvelope) -> bool:
    return event.event_type is EventType.DIRECT_MENTION


def _deterministic_reply_to_bot(event: EventEnvelope) -> bool:
    return event.event_type in {EventType.REPLY_TO_BOT, EventType.REPLY_TO_BOT_MESSAGE} or bool(
        event.metadata.get("reply_to_bot")
    )


def conservative_scene(event: EventEnvelope) -> SceneAnalysis:
    """Fail closed for unsolicited group humor while preserving explicit routing signals."""
    return SceneAnalysis(
        direct_mention=_deterministic_direct_mention(event),
        reply_to_bot=_deterministic_reply_to_bot(event),
        question_to_bot=False,
        command_intent=None,
        banter_score=0.0,
        seriousness_score=0.75,
        conflict_score=0.5,
        sensitivity_score=0.75,
        roast_opportunity=0.0,
        callback_opportunity=0.0,
        help_opportunity=0.0,
        memory_value=0.0,
        contradiction_score=0.0,
        commitment_signal=0.0,
        decision_signal=0.0,
    )


class SceneAnalyzer:
    def __init__(self, adapter, *, instructions: str | None = None) -> None:
        self.adapter = adapter
        self.instructions = instructions if instructions is not None else PROMPT_PATH.read_text(encoding="utf-8")

    def analyze(
        self,
        event: EventEnvelope,
        *,
        recent_context: str | None = None,
    ) -> SceneAnalysis:
        current_text = event.text or ""
        input_text = (
            f"CURRENT EVENT\n"
            f"type: {event.event_type.value}\n"
            f"scope: {event.scope_type.value}\n"
            f"text: {current_text}\n"
        )
        if recent_context:
            input_text += f"\nRECENT CONTEXT\n{recent_context}\n"

        try:
            result = self.adapter.generate_json(
                ModelRole.CLASSIFIER,
                input_text,
                schema_name="scene_analysis",
                schema=SCENE_ANALYSIS_SCHEMA,
                instructions=self.instructions,
                event_id=event.event_id,
                max_output_tokens=500,
            )
            parsed = dict(result.parsed or {})
            parsed["direct_mention"] = _deterministic_direct_mention(event)
            parsed["reply_to_bot"] = _deterministic_reply_to_bot(event)
            return SceneAnalysis(**parsed)
        except Exception:
            return conservative_scene(event)
