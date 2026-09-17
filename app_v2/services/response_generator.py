from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from app_v2.domain.enums import PrimaryAction, ResponseMode, ScopeType
from app_v2.services.context_builder import GenerationContext
from app_v2.services.model_router import ModelRole


class ResponseGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class GeneratedResponse:
    text: str
    model: str
    usage_id: str


class ResponseGenerator:
    def __init__(
        self,
        *,
        adapter: Any,
        personal_prompt_path: Path | None = None,
        group_prompt_path: Path | None = None,
        group_character_prompt_paths: Mapping[str, Path] | None = None,
    ) -> None:
        self.adapter = adapter
        root = Path(__file__).resolve().parents[1] / "prompts"
        self.personal_prompt_path = personal_prompt_path or root / "personal_generator.md"
        self.group_prompt_path = group_prompt_path or root / "group_generator.md"
        self.group_character_prompt_paths: dict[str, Path] = {
            "diamond_voice": root / "diamond_voice_generator.md",
        }
        if group_character_prompt_paths:
            self.group_character_prompt_paths.update(
                {str(key).strip().lower(): path for key, path in group_character_prompt_paths.items()}
            )

    def generate(self, context: GenerationContext) -> GeneratedResponse:
        decision = context.decision
        if decision.get("primary_action") != PrimaryAction.REPLY.value:
            raise ResponseGenerationError("ResponseGenerator may only run for primary_action=reply")

        mode = decision.get("mode")
        if mode == ResponseMode.GROUP_CALLBACK.value and not context.memories:
            raise ResponseGenerationError("group_callback requires at least one provided Memory Card")

        prompt = self._load_prompt(context)
        payload = self._build_payload(context)

        try:
            result = self.adapter.generate_text(
                ModelRole.GENERATOR,
                json.dumps(payload, ensure_ascii=False),
                instructions=prompt,
                event_id=context.event.get("event_id"),
                max_output_tokens=700,
            )
        except Exception as exc:
            raise ResponseGenerationError(f"generation failed: {type(exc).__name__}") from exc

        text = (result.text or "").strip()
        if not text:
            raise ResponseGenerationError("generator returned empty text")

        usage = result.usage
        return GeneratedResponse(
            text=text,
            model=usage.model,
            usage_id=usage.usage_id,
        )

    def _load_prompt(self, context: GenerationContext) -> str:
        if context.scope_type is ScopeType.PERSONAL:
            return self.personal_prompt_path.read_text(encoding="utf-8")

        action_state = context.action_state if isinstance(context.action_state, dict) else {}
        character_id = str(action_state.get("character_id") or "").strip().lower()
        path = self.group_character_prompt_paths.get(character_id, self.group_prompt_path)
        return path.read_text(encoding="utf-8")

    @staticmethod
    def _build_payload(context: GenerationContext) -> dict[str, Any]:
        personality = context.personality
        compact_personality = {
            key: personality[key]
            for key in (
                "mode", "directness", "brevity", "warmth", "pressure",
                "humor", "sarcasm", "roast", "profanity_level",
                "profanity_frequency", "initiative", "callback", "challenge",
                "care", "playfulness", "sensitivity",
            )
            if key in personality
        }

        return {
            "scope": {"type": context.scope_type.value, "id": context.scope_id},
            "event": context.event,
            "scene": context.scene,
            "decision": {
                "mode": context.decision.get("mode"),
                "reason_codes": context.decision.get("reason_codes", []),
                "target_user_id": context.target_user_id,
            },
            "personality": compact_personality,
            "hot_messages": list(context.hot_messages),
            "memories": [
                {
                    "id": memory.id,
                    "type": memory.memory_type,
                    "summary": memory.summary,
                    "confidence": memory.confidence,
                    "importance": memory.importance,
                    "evidence": list(memory.evidence),
                }
                for memory in context.memories
            ],
            "action_state": context.action_state,
        }
