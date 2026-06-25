from __future__ import annotations

from dataclasses import dataclass

try:
    from .config import AppConfig
    from .nenoy_engine import generate_response as generate_local_response
    from .prompt_loader import load_all_prompts
except ImportError:  # Allows direct script imports in local checks.
    from config import AppConfig
    from nenoy_engine import generate_response as generate_local_response
    from prompt_loader import load_all_prompts


@dataclass(frozen=True)
class ConversationContext:
    goal: str | None
    memory_summary: str = ""
    recent_messages: tuple[tuple[str, str], ...] = ()


def build_system_instructions() -> str:
    prompts = load_all_prompts()
    ordered_names = (
        "system_prompt",
        "response_algorithm",
        "state_classifier",
        "reaction_scenarios",
        "combat_dictionary",
    )
    sections = [prompts[name] for name in ordered_names if name in prompts]
    return "\n\n---\n\n".join(sections)


def build_user_input(message: str, context: ConversationContext) -> str:
    goal = context.goal or "Цель не указана"
    recent = "\n".join(
        f"{role}: {content}" for role, content in context.recent_messages[-8:]
    )
    if not recent:
        recent = "Нет предыдущих сообщений."

    summary = context.memory_summary or "Нет сохранённого резюме."
    return (
        f"Текущая цель пользователя:\n{goal}\n\n"
        f"Память по пользователю:\n{summary}\n\n"
        f"Последние сообщения:\n{recent}\n\n"
        f"Новое сообщение пользователя:\n{message}\n\n"
        "Ответь как НеНойBot: коротко, живо, с характером, жёстко к бездействию. "
        "Но сначала учти состояние пользователя. Не дави одинаково на усталость, слив, "
        "оффтоп и желание всё бросить.\n"
        "Каждый ответ держи в стиле тренера: иронично, с лёгким подколом и рабочей метафорой "
        "про старт/подход/таймер/разминку — без занудного менеджерского тона.\n"
        "Вдохновляй через действие, не через морали.\n"
        "Не обязан каждый раз заканчивать вопросом о сроке. Выбирай подходящий финал: "
        "микро-действие, точное время, выбор из двух вариантов, короткий контракт или "
        "честное закрытие дня без самообмана.\n"
        "Не повторяй одинаковый микро-шаг или тот же вопрос, если похожая формулировка была "
        "уже в последних сообщениях."
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

    def generate(self, message: str, context: ConversationContext) -> str:
        if not self.enabled:
            return generate_local_response(
                message,
                goal=context.goal,
                recent_messages=context.recent_messages,
            )

        response = self._get_client().responses.create(
            model=AppConfig.openai_model,
            instructions=build_system_instructions(),
            input=build_user_input(message, context),
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


def generate_ai_response(message: str, context: ConversationContext) -> str:
    try:
        return OpenAINenoyClient().generate(message, context)
    except Exception as exc:
        fallback = generate_local_response(
            message,
            goal=context.goal,
            recent_messages=context.recent_messages,
        )
        return f"{fallback}\n\nGPT временно не ответил: {type(exc).__name__}."
