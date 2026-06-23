from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib import error, parse, request

try:
    from .config import AppConfig
    from .memory_store import MemoryStore, create_memory_store
    from .openai_client import ConversationContext, generate_ai_response
except ImportError:  # Allows `python app/telegram_bot.py`.
    from config import AppConfig
    from memory_store import MemoryStore, create_memory_store
    from openai_client import ConversationContext, generate_ai_response


HELP_TEXT = (
    "НеНойBot онлайн.\n"
    "Кнопки:\n"
    "🎯 Задать цель — записать цель без команды\n"
    "✅ Отчёт — сдать результат\n"
    "🔥 Пинок — получить ближайший шаг\n"
    "🧹 Сбросить цель — очистить цель\n\n"
    "Команды тоже работают: /goal, /clear_goal, /help.\n"
    "Сначала цель. Потом действие."
)

BUTTON_SET_GOAL = "🎯 Задать цель"
BUTTON_REPORT = "✅ Отчёт"
BUTTON_KICK = "🔥 Пинок"
BUTTON_HELP = "📌 Меню"
BUTTON_CLEAR_GOAL = "🧹 Сбросить цель"

MAIN_KEYBOARD = {
    "keyboard": [
        [{"text": BUTTON_SET_GOAL}, {"text": BUTTON_REPORT}],
        [{"text": BUTTON_KICK}, {"text": BUTTON_HELP}],
        [{"text": BUTTON_CLEAR_GOAL}],
    ],
    "resize_keyboard": True,
    "one_time_keyboard": False,
    "is_persistent": True,
}

BOT_COMMANDS = (
    {"command": "start", "description": "Запустить НеНойBot"},
    {"command": "goal", "description": "Задать цель: /goal результат + срок"},
    {"command": "clear_goal", "description": "Сбросить цель"},
    {"command": "help", "description": "Показать команды"},
)


@dataclass
class TelegramRuntimeState:
    awaiting_goal: set[int] = field(default_factory=set)
    awaiting_report: set[int] = field(default_factory=set)

    def wait_for_goal(self, chat_id: int) -> None:
        self.awaiting_goal.add(chat_id)
        self.awaiting_report.discard(chat_id)

    def wait_for_report(self, chat_id: int) -> None:
        self.awaiting_report.add(chat_id)
        self.awaiting_goal.discard(chat_id)

    def clear(self, chat_id: int) -> None:
        self.awaiting_goal.discard(chat_id)
        self.awaiting_report.discard(chat_id)


class TelegramAPI:
    def __init__(self, token: str) -> None:
        self.base_url = f"https://api.telegram.org/bot{token}/"

    def request(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        body = parse.urlencode(payload or {}).encode("utf-8")
        api_request = request.Request(self.base_url + method, data=body, method="POST")

        try:
            with request.urlopen(api_request, timeout=90) as response:
                data = json.loads(response.read().decode("utf-8"))
        except error.URLError as exc:
            raise RuntimeError(f"Telegram request failed: {exc}") from exc

        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data}")

        return data.get("result")

    def delete_webhook(self) -> None:
        self.request("deleteWebhook", {"drop_pending_updates": "false"})

    def set_commands(self) -> None:
        self.request("setMyCommands", {"commands": json.dumps(BOT_COMMANDS, ensure_ascii=False)})

    def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": str(timeout)}
        if offset is not None:
            payload["offset"] = str(offset)
        return self.request("getUpdates", payload)

    def send_message(self, chat_id: int, text: str) -> None:
        self.request(
            "sendMessage",
            {
                "chat_id": str(chat_id),
                "text": text,
                "reply_markup": json.dumps(MAIN_KEYBOARD, ensure_ascii=False),
            },
        )


def run_startup_action(name: str, action) -> None:
    try:
        action()
    except RuntimeError as exc:
        print(f"Startup action skipped: {name}: {exc}")


def extract_text_message(update: dict[str, Any]) -> tuple[int, str] | None:
    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return None

    chat = message.get("chat")
    text = message.get("text")
    if not isinstance(chat, dict) or not isinstance(text, str):
        return None

    chat_id = chat.get("id")
    if not isinstance(chat_id, int):
        return None

    return chat_id, text.strip()


def build_reply(
    chat_id: int,
    text: str,
    store: MemoryStore,
    runtime_state: TelegramRuntimeState | None = None,
) -> str:
    if runtime_state is None:
        runtime_state = TelegramRuntimeState()

    if not text:
        return "Пусто. Назови цель или действие."

    if text.startswith("/start") or text.startswith("/help") or text == BUTTON_HELP:
        runtime_state.clear(chat_id)
        return HELP_TEXT

    if text == BUTTON_SET_GOAL:
        runtime_state.wait_for_goal(chat_id)
        return "Напиши цель одним сообщением: результат + срок. Без этого дальше шум."

    if text == BUTTON_REPORT:
        runtime_state.wait_for_report(chat_id)
        return "Напиши отчёт одним сообщением: что сделал и что закрываешь следующим."

    if text == BUTTON_CLEAR_GOAL:
        runtime_state.clear(chat_id)
        store.clear_goal(chat_id)
        return "Цель сброшена. Без цели это шум. Нажми «🎯 Задать цель»."

    if text == BUTTON_KICK:
        runtime_state.clear(chat_id)
        text = "Мне нужна мотивация"

    if text.startswith("/goal"):
        goal = text.removeprefix("/goal").strip()
        if not goal:
            return "Цель пустая. Напиши так: /goal результат + срок."
        runtime_state.clear(chat_id)
        store.set_goal(chat_id, goal)
        store.append_message(chat_id, "user", text)
        store.append_message(chat_id, "assistant", f"Цель принята: {goal}. Теперь ближайший шаг.")
        return f"Цель принята: {goal}. Теперь ближайший шаг."

    if text.startswith("/clear_goal"):
        runtime_state.clear(chat_id)
        store.clear_goal(chat_id)
        return "Цель сброшена. Без цели это шум. Нажми «🎯 Задать цель»."

    if chat_id in runtime_state.awaiting_goal:
        runtime_state.clear(chat_id)
        store.set_goal(chat_id, text)
        store.append_message(chat_id, "user", f"Цель: {text}")
        store.append_message(chat_id, "assistant", f"Цель принята: {text}. Теперь ближайший шаг.")
        return f"Цель принята: {text}. Теперь ближайший шаг."

    if chat_id in runtime_state.awaiting_report:
        runtime_state.clear(chat_id)
        text = f"Отчёт: {text}"

    context = ConversationContext(
        goal=store.get_goal(chat_id),
        memory_summary=store.get_summary(chat_id),
        recent_messages=tuple(store.recent_messages(chat_id, limit=8)),
    )
    reply = generate_ai_response(text, context)
    store.append_message(chat_id, "user", text)
    store.append_message(chat_id, "assistant", reply)
    return reply


def run_telegram_bot() -> None:
    if not AppConfig.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required.")

    api = TelegramAPI(AppConfig.telegram_bot_token)
    store = create_memory_store()
    runtime_state = TelegramRuntimeState()
    offset: int | None = None

    run_startup_action("deleteWebhook", api.delete_webhook)
    run_startup_action("setMyCommands", api.set_commands)
    print("НеНойBot Telegram worker started.")

    while True:
        try:
            updates = api.get_updates(offset=offset, timeout=AppConfig.telegram_poll_timeout)
            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = update_id + 1

                extracted = extract_text_message(update)
                if extracted is None:
                    continue

                chat_id, text = extracted
                api.send_message(chat_id, build_reply(chat_id, text, store, runtime_state))
        except RuntimeError as exc:
            print(exc)
            time.sleep(5)


if __name__ == "__main__":
    run_telegram_bot()
