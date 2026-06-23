from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib import error, parse, request

try:
    from .config import AppConfig
    from .nenoy_engine import generate_response
except ImportError:  # Allows `python app/telegram_bot.py`.
    from config import AppConfig
    from nenoy_engine import generate_response


HELP_TEXT = (
    "НеНойBot онлайн.\n"
    "Команды:\n"
    "/goal цель — зафиксировать цель\n"
    "/clear_goal — сбросить цель\n"
    "/help — команды\n\n"
    "Сначала цель. Потом действие."
)


@dataclass
class TelegramSessionStore:
    goals: dict[int, str] = field(default_factory=dict)

    def get_goal(self, chat_id: int) -> str | None:
        return self.goals.get(chat_id) or AppConfig.default_goal

    def set_goal(self, chat_id: int, goal: str) -> None:
        self.goals[chat_id] = goal

    def clear_goal(self, chat_id: int) -> None:
        self.goals.pop(chat_id, None)


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

    def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": str(timeout)}
        if offset is not None:
            payload["offset"] = str(offset)
        return self.request("getUpdates", payload)

    def send_message(self, chat_id: int, text: str) -> None:
        self.request("sendMessage", {"chat_id": str(chat_id), "text": text})


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


def build_reply(chat_id: int, text: str, store: TelegramSessionStore) -> str:
    if not text:
        return "Пусто. Назови цель или действие."

    if text.startswith("/start") or text.startswith("/help"):
        return HELP_TEXT

    if text.startswith("/goal"):
        goal = text.removeprefix("/goal").strip()
        if not goal:
            return "Цель пустая. Напиши так: /goal результат + срок."
        store.set_goal(chat_id, goal)
        return f"Цель принята: {goal}. Теперь ближайший шаг."

    if text.startswith("/clear_goal"):
        store.clear_goal(chat_id)
        return "Цель сброшена. Без цели это шум. Напиши /goal результат + срок."

    return generate_response(text, goal=store.get_goal(chat_id))


def run_telegram_bot() -> None:
    if not AppConfig.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required.")

    api = TelegramAPI(AppConfig.telegram_bot_token)
    store = TelegramSessionStore()
    offset: int | None = None

    api.delete_webhook()
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
                api.send_message(chat_id, build_reply(chat_id, text, store))
        except RuntimeError as exc:
            print(exc)
            time.sleep(5)


if __name__ == "__main__":
    run_telegram_bot()
