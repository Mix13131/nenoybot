from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


_MEMORY_SAVE_RE = re.compile(
    r"(?<!не )\b(?:запомнил|зафиксировал\s+(?:это\s+)?в\s+памят\w*|сохранил\s+(?:это\s+)?в\s+памят\w*)\b",
    flags=re.IGNORECASE,
)
_TASK_CREATE_RE = re.compile(
    r"(?:"
    r"(?<!не )\b(?:создал|добавил|записал|вн[её]с|сохранил)\s+(?:эту\s+)?задач\w*"
    r"|\bзадач\w*\s+(?:создана|добавлена|сохранена)\b"
    r")",
    flags=re.IGNORECASE,
)
_REMINDER_CREATE_RE = re.compile(
    r"(?:"
    r"(?<!не )\b(?:поставил|создал|добавил|запланировал)\s+(?:тебе\s+)?напоминан\w*"
    r"|(?<!не )\bбуду\s+(?:тебе\s+)?напоминать\b"
    r"|\bнапомню\s+(?:тебе\s+)?(?:через|завтра|сегодня|в\s+\d|к\s+\d)"
    r"|\bпну\b.{0,50}(?:через|завтра|сегодня|\d{1,2}[./-]\d{1,2}|\d+\s*(?:дн|час|минут))"
    r")",
    flags=re.IGNORECASE | re.DOTALL,
)
_REMINDER_CANCEL_RE = re.compile(
    r"(?:"
    r"(?<!не )\b(?:отменил|остановил|выключил)\s+(?:это\s+)?напоминан\w*"
    r"|\bбольше\s+не\s+буду\s+(?:тебе\s+)?напоминать\b"
    r")",
    flags=re.IGNORECASE,
)


class ResponseGenerator:
    def __init__(
        self,
        *,
        adapter: Any,
        personal_prompt_path: Path | None = None,
        group_prompt_path: Path | None = None,
    ) -> None:
        self.adapter = adapter
        root = Path(__file__).resolve().parents[1] / "prompts"
        self.personal_prompt_path = personal_prompt_path or root / "personal_generator.md"
        self.group_prompt_path = group_prompt_path or root / "group_generator.md"

    def generate(self, context: GenerationContext) -> GeneratedResponse:
        decision = context.decision
        if decision.get("primary_action") != PrimaryAction.REPLY.value:
            raise ResponseGenerationError("ResponseGenerator may only run for primary_action=reply")

        mode = decision.get("mode")
        if mode == ResponseMode.GROUP_CALLBACK.value and not context.memories:
            raise ResponseGenerationError("group_callback requires at least one provided Memory Card")

        prompt = self._load_prompt(context.scope_type)
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

        text = self._enforce_operation_receipts(text, context.action_state)
        usage = result.usage
        return GeneratedResponse(
            text=text,
            model=usage.model,
            usage_id=usage.usage_id,
        )

    def _load_prompt(self, scope_type: ScopeType) -> str:
        path = self.personal_prompt_path if scope_type is ScopeType.PERSONAL else self.group_prompt_path
        return path.read_text(encoding="utf-8")

    @staticmethod
    def _receipt(action_state: dict[str, Any], kind: str) -> dict[str, Any]:
        receipts = action_state.get("operation_receipts")
        if not isinstance(receipts, dict):
            return {}
        value = receipts.get(kind)
        return dict(value) if isinstance(value, dict) else {}

    @classmethod
    def _memory_save_confirmed(cls, action_state: dict[str, Any]) -> bool:
        receipt = cls._receipt(action_state, "memory")
        return bool(
            receipt.get("status") == "succeeded"
            and receipt.get("changed") is True
            and receipt.get("written_ids")
        )

    @classmethod
    def _task_change_confirmed(cls, action_state: dict[str, Any]) -> bool:
        receipt = cls._receipt(action_state, "task")
        return bool(
            receipt.get("status") == "succeeded"
            and receipt.get("changed") is True
            and receipt.get("entity_ids")
        )

    @classmethod
    def _reminder_change_confirmed(cls, action_state: dict[str, Any], operation: str) -> bool:
        receipt = cls._receipt(action_state, "reminder")
        if receipt.get("status") != "succeeded" or receipt.get("changed") is not True:
            return False
        if receipt.get("operation") != operation:
            return False
        if operation == "create":
            return bool(receipt.get("entity_ids"))
        if operation == "cancel":
            return int(receipt.get("cancelled_count") or 0) > 0
        return False

    @classmethod
    def _enforce_operation_receipts(cls, text: str, action_state: dict[str, Any]) -> str:
        violations: list[str] = []
        if _MEMORY_SAVE_RE.search(text) and not cls._memory_save_confirmed(action_state):
            violations.append("memory")
        if _TASK_CREATE_RE.search(text) and not cls._task_change_confirmed(action_state):
            violations.append("task")
        if _REMINDER_CREATE_RE.search(text) and not cls._reminder_change_confirmed(action_state, "create"):
            violations.append("reminder_create")
        if _REMINDER_CANCEL_RE.search(text) and not cls._reminder_change_confirmed(action_state, "cancel"):
            violations.append("reminder_cancel")
        if not violations:
            return text
        return cls._truthful_receipt_fallback(action_state, violations)

    @classmethod
    def _truthful_receipt_fallback(
        cls,
        action_state: dict[str, Any],
        violations: list[str],
    ) -> str:
        parts: list[str] = []
        memory = cls._receipt(action_state, "memory")
        task = cls._receipt(action_state, "task")
        reminder = cls._receipt(action_state, "reminder")

        if memory.get("status") == "succeeded" and memory.get("changed") is True:
            written_count = len(memory.get("written_ids") or [])
            forgotten_count = len(memory.get("forgotten_ids") or [])
            if written_count:
                parts.append(
                    "В память зафиксировал." if written_count == 1 else f"В память зафиксировал {written_count} записи."
                )
            elif forgotten_count:
                parts.append("Запись из памяти убрал.")
        elif "memory" in violations:
            parts.append("В память это не записано.")

        if "task" in violations:
            if task.get("status") == "failed":
                parts.append("Задачу создать не удалось.")
            else:
                parts.append("Задачу не создавал.")

        if "reminder_create" in violations:
            status = reminder.get("status")
            if status == "needs_clarification":
                parts.append("Напоминание не поставлено — нужно уточнить время или адресата.")
            elif status == "failed":
                parts.append("Напоминание не создано: операция завершилась ошибкой.")
            else:
                parts.append("Напоминание не ставил.")

        if "reminder_cancel" in violations:
            if reminder.get("status") == "failed":
                parts.append("Напоминание не остановлено: операция завершилась ошибкой.")
            else:
                parts.append("Активное напоминание не остановлено.")

        if not parts:
            parts.append("Понял, но подтверждённого действия здесь не было.")
        parts.append("Без магии в отчётности 😏")
        return " ".join(parts)

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
