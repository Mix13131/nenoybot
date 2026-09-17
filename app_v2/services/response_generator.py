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


_CLAIM_BOUNDARY = r"(?:^|[.!?;:,]\s+)"
_MEMORY_SAVE_RE = re.compile(
    _CLAIM_BOUNDARY
    + r"(?:(?:я|мы)\s+)?(?:"
    r"(?:запомнил|запомню)\b"
    r"|(?:зафиксировал|зафиксирую|сохранил|сохраню)\s+(?:это\s+)?в\s+памят\w*"
    r"|в\s+памят\w*\s+(?:зафиксировал|зафиксирую|сохранил|сохраню)\b"
    r"|(?:запис(?:ь|и)|это)\s+(?:успешно\s+)?(?:сохранен(?:а|о|ы)|зафиксирован(?:а|о|ы))\s+в\s+памят\w*"
    r"|в\s+памят\w*\s+(?:успешно\s+)?(?:сохранен(?:а|о|ы)|зафиксирован(?:а|о|ы))\b"
    r")",
    flags=re.IGNORECASE,
)
_MEMORY_FORGET_RE = re.compile(
    _CLAIM_BOUNDARY
    + r"(?:(?:я|мы)\s+)?(?:"
    r"(?:забыл|забуду|удалил|удалю|убрал|уберу)\s+(?:это\s+)?(?:из\s+)?памят\w*"
    r"|(?:запис(?:ь|и)|это)\s+(?:из\s+памят\w*\s+)?(?:успешно\s+)?(?:удален(?:а|о|ы)|убран(?:а|о|ы)|забыт(?:а|о|ы))\b"
    r"|из\s+памят\w*\s+(?:успешно\s+)?(?:удален(?:о|ы)|убран(?:о|ы)|забыт(?:о|ы))\b"
    r")",
    flags=re.IGNORECASE,
)
_TASK_CREATE_RE = re.compile(
    _CLAIM_BOUNDARY
    + r"(?:(?:я|мы)\s+)?(?:"
    r"(?:создал|создам|добавил|добавлю|записал|запишу|вн[её]с|внесу|сохранил|сохраню)"
    r"\s+(?:тебе\s+)?(?:эту\s+)?задач\w*"
    r"|задач\w*\s+(?:я\s+)?(?:создал|создам|добавил|добавлю|записал|запишу|вн[её]с|внесу|сохранил|сохраню)\b"
    r"|задач\w*\s+(?:успешно\s+)?(?:создан(?:а|ы)|добавлен(?:а|ы)|записан(?:а|ы)|внесен(?:а|ы)|сохранен(?:а|ы))\b"
    r")",
    flags=re.IGNORECASE,
)
_REMINDER_CREATE_RE = re.compile(
    _CLAIM_BOUNDARY
    + r"(?:(?:я|мы)\s+)?(?:"
    r"(?:поставил|поставлю|создал|создам|добавил|добавлю|запланировал|запланирую|настроил|настрою)"
    r"\s+(?:тебе\s+)?напоминан\w*"
    r"|напоминан\w*\s+(?:я\s+)?(?:поставил|поставлю|создал|создам|добавил|добавлю|запланировал|запланирую|настроил|настрою)\b"
    r"|напоминан\w*\s+(?:успешно\s+)?(?:поставлен(?:о|ы)|создан(?:о|ы)|добавлен(?:о|ы)|запланирован(?:о|ы)|настроен(?:о|ы))\b"
    r"|(?:тебе\s+)?буду\s+(?:тебе\s+)?напоминать\b"
    r"|(?:тебе\s+)?напомню\s+(?:тебе\s+)?(?:через|завтра|сегодня|в\s+\d|к\s+\d)"
    r"|(?:тебе\s+)?пну\b.{0,50}(?:через|завтра|сегодня|\d{1,2}[./-]\d{1,2}|\d+\s*(?:дн|час|минут))"
    r")",
    flags=re.IGNORECASE | re.DOTALL,
)
_REMINDER_CANCEL_RE = re.compile(
    _CLAIM_BOUNDARY
    + r"(?:(?:я|мы)\s+)?(?:"
    r"(?:отменил|отменю|остановил|остановлю|выключил|выключу)\s+(?:это\s+)?напоминан\w*"
    r"|напоминан\w*\s+(?:я\s+)?(?:отменил|отменю|остановил|остановлю|выключил|выключу)\b"
    r"|напоминан\w*\s+(?:успешно\s+)?(?:отменен(?:о|ы)|остановлен(?:о|ы)|выключен(?:о|ы))\b"
    r"|больше\s+не\s+буду\s+(?:тебе\s+)?напоминать\b"
    r")",
    flags=re.IGNORECASE,
)
_QUOTED_TEXT_RE = re.compile(r"«[^»]*»|“[^”]*”|\"[^\"]*\"", flags=re.DOTALL)
_NEXT_TOKEN_RE = re.compile(r"^\s+(@?[A-Za-zА-Яа-яЁё0-9_-]+)")
_OBJECT_FIRST_PREFIXES = (
    "задач",
    "напоминан",
    "в память",
    "памят",
    "запись",
    "записи",
    "из памяти",
)
_BOT_CLAIM_CONTINUATIONS = {
    "вчера", "сегодня", "завтра", "сейчас", "уже", "только", "успешно",
    "автоматически", "быстро", "недавно", "тебе", "вам", "для", "на",
    "в", "к", "по", "с", "из", "до", "после", "через", "без", "как",
    "при", "от", "под", "над", "между", "этому", "этой", "это",
}


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

    @staticmethod
    def _claim_scan_text(text: str) -> str:
        return _QUOTED_TEXT_RE.sub(" ", text)

    @staticmethod
    def _is_third_party_object_first(match: re.Match[str], text: str) -> bool:
        fragment = match.group(0).lower().lstrip(" .!?;:,")
        if not fragment.startswith(_OBJECT_FIRST_PREFIXES):
            return False
        token_match = _NEXT_TOKEN_RE.match(text[match.end() :])
        if token_match is None:
            return False
        token = token_match.group(1).lower().lstrip("@")
        # After an object-first action, an ordinary subject noun/name/pronoun
        # means the sentence describes somebody else's action. Time/location/
        # manner/preposition continuations still represent a bot action claim.
        return token not in _BOT_CLAIM_CONTINUATIONS

    @classmethod
    def _has_bot_action_claim(cls, pattern: re.Pattern[str], text: str) -> bool:
        for match in pattern.finditer(text):
            if cls._is_third_party_object_first(match, text):
                continue
            return True
        return False

    @classmethod
    def _memory_save_confirmed(cls, action_state: dict[str, Any]) -> bool:
        receipt = cls._receipt(action_state, "memory")
        return bool(
            receipt.get("status") == "succeeded"
            and receipt.get("changed") is True
            and receipt.get("written_ids")
        )

    @classmethod
    def _memory_forget_confirmed(cls, action_state: dict[str, Any]) -> bool:
        receipt = cls._receipt(action_state, "memory")
        return bool(
            receipt.get("status") == "succeeded"
            and receipt.get("changed") is True
            and receipt.get("forgotten_ids")
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
        claim_text = cls._claim_scan_text(text)
        violations: list[str] = []
        if cls._has_bot_action_claim(_MEMORY_SAVE_RE, claim_text) and not cls._memory_save_confirmed(action_state):
            violations.append("memory")
        if cls._has_bot_action_claim(_MEMORY_FORGET_RE, claim_text) and not cls._memory_forget_confirmed(action_state):
            violations.append("memory_forget")
        if cls._has_bot_action_claim(_TASK_CREATE_RE, claim_text) and not cls._task_change_confirmed(action_state):
            violations.append("task")
        if cls._has_bot_action_claim(_REMINDER_CREATE_RE, claim_text) and not cls._reminder_change_confirmed(action_state, "create"):
            violations.append("reminder_create")
        if cls._has_bot_action_claim(_REMINDER_CANCEL_RE, claim_text) and not cls._reminder_change_confirmed(action_state, "cancel"):
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

        written_count = len(memory.get("written_ids") or [])
        forgotten_count = len(memory.get("forgotten_ids") or [])
        if memory.get("status") == "succeeded" and memory.get("changed") is True:
            if written_count:
                parts.append(
                    "В память зафиксировал." if written_count == 1 else f"В память зафиксировал {written_count} записи."
                )
            elif forgotten_count:
                parts.append("Запись из памяти убрал.")
        elif memory.get("status") == "partial" and memory.get("changed") is True:
            if written_count:
                parts.append(
                    f"Часть памяти записана: {written_count} из подтверждённых записей сохранились, но операция завершилась не полностью."
                )
            elif forgotten_count:
                parts.append(
                    f"Часть изменений памяти выполнена: {forgotten_count}, но операция завершилась не полностью."
                )
        elif "memory" in violations:
            parts.append("В память это не записано.")
        if "memory_forget" in violations and not forgotten_count:
            if memory.get("status") == "failed":
                parts.append("Из памяти удалить не удалось.")
            else:
                parts.append("Из памяти ничего не удалял.")

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