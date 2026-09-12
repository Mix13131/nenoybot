from __future__ import annotations

from dataclasses import dataclass

try:
    from .config import AppConfig
    from .nenoy_engine import generate_response as generate_local_response
    from .prompt_loader import load_prompt
except ImportError:  # Allows direct script imports in local checks.
    from config import AppConfig
    from nenoy_engine import generate_response as generate_local_response
    from prompt_loader import load_prompt


@dataclass(frozen=True)
class ConversationContext:
    goal: str | None
    memory_summary: str = ""
    recent_messages: tuple[tuple[str, str], ...] = ()


def build_system_instructions() -> str:
    """Single source of truth for the coach persona."""
    return load_prompt("persona_v3.md")


def build_support_instructions() -> str:
    # Lightness mode intentionally stays independent from Trainer personas.
    return (AppConfig.project_root / "app" / "support_system_prompt.md").read_text(encoding="utf-8")


def build_user_input(message: str, context: ConversationContext) -> str:
    goal = context.goal or "Цель не указана"
    recent = "\n".join(f"{role}: {content}" for role, content in context.recent_messages[-8:])
    recent = recent or "Нет предыдущих сообщений."
    summary = context.memory_summary or "Нет сохранённого резюме."
    return (
        f"Текущая цель пользователя:\n{goal}\n\n"
        f"Память по пользователю:\n{summary}\n\n"
        f"Последние сообщения:\n{recent}\n\n"
        f"Новое сообщение пользователя:\n{message}\n\n"
        "Ты сейчас в режиме 🔥 Тренер и работаешь по Persona v3. "
        "Главное ДНК: 'Ной не ныл — и ты не ной', но не повторяй фразу механически. "
        "Базовый градус сарказма высокий: примерно 7/10. Лёгкий буллинг направляй на отмазку, поведение и нелепость ситуации, а не на личность. "
        "Если в последних сообщениях уже названа конкретная цель или обязательство, считай её активной. "
        "Если новая реплика уходит от этой цели в расслабон, перенос, бесконечную подготовку или очевидную прокрастинацию — поймай противоречие и ответь по формуле ПОДЪЁБ → ПРАВДА → ХОД. "
        "ХОД не обязателен, если точный укол уже сделал работу. "
        "Если активной цели нет и человек просто болтает — не выдумывай избегание. "
        "Не используй язык режима 🌿 Лёгкость и не скатывайся в бережный коучинг. "
        "Не превращай простой ответ в консультацию, психологический разбор или длинный список. "
        "Не выдумывай факты, задачи, сроки или детали. Не повторяй недавние метафоры и шутки."
    )


def build_support_user_input(message: str, context: ConversationContext) -> str:
    recent = "\n".join(f"{role}: {content}" for role, content in context.recent_messages[-8:])
    return (
        f"Последние сообщения режима support:\n{recent or 'Нет предыдущих сообщений.'}\n\n"
        f"Новое сообщение:\n{message}\n\n"
        "Ответь в режиме поддержки. Не предполагай наличие цели или проекта и не создавай напоминание."
    )


class OpenAINenoyClient:
    def __init__(self) -> None:
        self._client = None

    @property
    def enabled(self) -> bool:
        return bool(AppConfig.openai_api_key)

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - dependency exists in deploy.
                raise RuntimeError("openai package is required when OPENAI_API_KEY is set.") from exc
            self._client = OpenAI(api_key=AppConfig.openai_api_key)
        return self._client

    def generate(self, message: str, context: ConversationContext, mode: str = "coach") -> str:
        if not self.enabled:
            if mode == "support":
                return support_fallback(message)
            return generate_local_response(
                message,
                goal=context.goal,
                recent_messages=context.recent_messages,
            )

        response = self._get_client().responses.create(
            model=AppConfig.openai_model,
            instructions=build_support_instructions() if mode == "support" else build_system_instructions(),
            input=build_support_user_input(message, context) if mode == "support" else build_user_input(message, context),
            reasoning={"effort": "low"},
            text={"verbosity": "low"},
        )

        text = getattr(response, "output_text", "") or ""
        return (
            text.strip()
            or generate_local_response(
                message,
                goal=context.goal,
                recent_messages=context.recent_messages,
            )
        )


def support_fallback(message: str) -> str:
    if any(word in message.casefold() for word in ("нет сил", "устал", "устала", "выгорел")):
        return "Тогда без дополнительного задания. Необязательное можно отложить; разбираться с ним сейчас не требуется."
    return "Не обязательно превращать это в экзамен. Можно разобраться по дороге и попробовать один естественный вариант — без задачи кого-то впечатлить."


def generate_ai_response(message: str, context: ConversationContext, mode: str = "coach") -> str:
    try:
        return OpenAINenoyClient().generate(message, context, mode=mode)
    except Exception as exc:
        fallback = support_fallback(message) if mode == "support" else generate_local_response(
            message,
            goal=context.goal,
            recent_messages=context.recent_messages,
        )
        return f"{fallback}\n\nGPT временно не ответил: {type(exc).__name__}."
