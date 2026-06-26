from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib import error, parse, request

try:
    from .config import AppConfig
    from .memory_store import MemoryStore, create_memory_store
    from .openai_client import ConversationContext, generate_ai_response
    from .reminders import (
        Commitment,
        format_due_at,
        get_timezone,
        parse_commitment,
        build_reminder_message,
    )
    from .style_guard import (
        find_botlike_phrases,
        find_forbidden_style_phrases,
        is_human_style_response,
    )
except ImportError:  # Allows `python app/telegram_bot.py`.
    from config import AppConfig
    from memory_store import MemoryStore, create_memory_store
    from openai_client import ConversationContext, generate_ai_response
    from reminders import (
        Commitment,
        format_due_at,
        get_timezone,
        parse_commitment,
        build_reminder_message,
    )
    from style_guard import (
        find_botlike_phrases,
        find_forbidden_style_phrases,
        is_human_style_response,
    )


logger = logging.getLogger(__name__)


HELP_TEXT = (
    "НеНойBot онлайн.\n"
    "Кнопки:\n"
    "🎯 Задать цель — записать цель без команды\n"
    "✅ Отчёт — сдать результат\n"
    "🔥 Пинок — получить ближайший шаг\n"
    "🧹 Сбросить цель — очистить цель\n\n"
    "Напоминание: напиши срок вроде «сегодня в 19:00», «завтра до 12:00» или «через 30 минут».\n"
    "В срок я сам приду за отчётом.\n\n"
    "Команды тоже работают: /goal, /clear_goal, /help.\n"
    "Сначала фокус. Потом пинок. Потом результат."
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

STYLE_GUARD_FALLBACKS = (
    "Стоп, я опять звучал как будильник с тремя словами. Пересобираю: что по факту прямо сейчас? 🔥",
    "Заладил — да. Снимаю автопилот. Дай факт: что сделано и что осталось? 🔧",
    "Поймал. Без попугайства: где результат, где затык? 🧱",
)
STYLE_GUARD_FALLBACK = STYLE_GUARD_FALLBACKS[0]

META_COMPLAINT_MARKERS = (
    "почему ты не напомнил",
    "почему ты не напомнишь",
    "почему не напомнишь",
    "почему напоминалка не работает",
    "напоминалка не работает",
    "не сработала напоминалка",
    "не пришло напоминание",
    "ты проигнорил",
    "ты заладил",
    "ты не напомнил",
    "ты не напомнишь",
    "одно и то же",
    "не приятно",
    "неприятно",
    "вообще неприятно",
)

META_COMPLAINT_REPLY = (
    "Да, тут косяк на моей стороне: напоминалка не прозвенела, "
    "а потом я ещё включил режим попугая.\n\n"
    "Фиксирую баг: проверяем, было ли событие на нужное время в памяти. "
    "Сейчас по задаче: дай факт, что собрано, и я помогу добить остаток без цирка. 🔧"
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

    def _send_raw_message(self, chat_id: int, text: str) -> None:
        self.request(
            "sendMessage",
            {
                "chat_id": str(chat_id),
                "text": text,
                "reply_markup": json.dumps(MAIN_KEYBOARD, ensure_ascii=False),
            },
        )

    def send_guarded_message(
        self,
        chat_id: int,
        text: str,
        recent_messages: tuple[tuple[str, str], ...] = (),
    ) -> None:
        guarded_text = prepare_outgoing_text(chat_id, text, recent_messages=recent_messages)
        self._send_raw_message(chat_id, guarded_text)

    def send_message(self, chat_id: int, text: str) -> None:
        self.send_guarded_message(chat_id, text)


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


def prepare_outgoing_text(
    chat_id: int,
    text: str,
    recent_messages: tuple[tuple[str, str], ...] = (),
) -> str:
    violations = find_forbidden_style_phrases(text)
    for violation in violations:
        logger.warning(
            'Style guard violation chat_id=%s pattern="%s" reason="%s"',
            chat_id,
            violation["pattern"],
            violation["reason"],
        )

    botlike_violations = find_botlike_phrases(text)
    for phrase in botlike_violations:
        logger.warning('Style guard violation: "%s"', phrase)

    if violations or not is_human_style_response(text):
        return pick_style_guard_fallback(recent_messages, text)

    return text


def pick_style_guard_fallback(
    recent_messages: tuple[tuple[str, str], ...] = (),
    rejected_text: str = "",
) -> str:
    last_assistant_text = ""
    for role, content in reversed(recent_messages):
        if role == "assistant":
            last_assistant_text = content
            break

    for fallback in STYLE_GUARD_FALLBACKS:
        if fallback != last_assistant_text and fallback != rejected_text:
            return fallback
    return STYLE_GUARD_FALLBACK


def is_meta_complaint(text: str) -> bool:
    normalized = text.casefold()
    return any(marker in normalized for marker in META_COMPLAINT_MARKERS)


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

    reminder_source_text = text

    if is_meta_complaint(text):
        runtime_state.clear(chat_id)
        store.append_message(chat_id, "user", text)
        store.append_message(chat_id, "assistant", META_COMPLAINT_REPLY)
        return META_COMPLAINT_REPLY

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
        reminder_note = schedule_reminder_if_found(chat_id, goal, store)
        store.append_message(chat_id, "user", text)
        response = f"{goal}. Не обсуждаем, выполняем. первый шаг сейчас?"
        if reminder_note:
            response = f"{response}\n\n{reminder_note}"
        store.append_message(chat_id, "assistant", response)
        return response

    if text.startswith("/clear_goal"):
        runtime_state.clear(chat_id)
        store.clear_goal(chat_id)
        return "Цель сброшена. Без цели это шум. Нажми «🎯 Задать цель»."

    if chat_id in runtime_state.awaiting_goal:
        runtime_state.clear(chat_id)
        store.set_goal(chat_id, text)
        reminder_note = schedule_reminder_if_found(chat_id, text, store)
        store.append_message(chat_id, "user", f"Цель: {text}")
        response = f"{text}. Не обсуждаем, выполняем. первый шаг сейчас?"
        if reminder_note:
            response = f"{response}\n\n{reminder_note}"
        store.append_message(chat_id, "assistant", response)
        return response

    if chat_id in runtime_state.awaiting_report:
        runtime_state.clear(chat_id)
        reminder_source_text = text
        text = f"Отчёт: {text}"

    context = ConversationContext(
        goal=store.get_goal(chat_id),
        memory_summary=store.get_summary(chat_id),
        recent_messages=tuple(store.recent_messages(chat_id, limit=8)),
    )
    reply = generate_ai_response(text, context)

    reminder_note = schedule_reminder_if_found(chat_id, reminder_source_text, store)
    if reminder_note:
        reply = f"{reply}\n\n{reminder_note}"
    store.append_message(chat_id, "user", text)
    store.append_message(chat_id, "assistant", reply)
    return reply


def schedule_reminder_if_found(chat_id: int, text: str, store: MemoryStore) -> str | None:
    commitment = parse_commitment(text, timezone_name=AppConfig.timezone)
    if commitment is None:
        return None

    timezone = get_timezone(AppConfig.timezone)
    now = datetime.now(timezone)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone)

    if commitment.active_checkin_time:
        store.cancel_previous_checkins_for_task(chat_id, commitment.task_text)

    if commitment.reminder_time:
        store.add_reminder(chat_id, commitment.task_text, commitment.reminder_time, reminder_type="reminder")
    if commitment.active_checkin_time:
        store.add_reminder(
            chat_id,
            commitment.task_text,
            commitment.active_checkin_time,
            reminder_type="checkin",
        )

    confirmation = _format_commitment_confirmation(commitment, now)
    if confirmation is None:
        return None

    return (
        f"{confirmation} "
        "Это не дата в заметках. Это проверка факта. "
        "Вернешься с результатом или с новой отговоркой? 🔥"
    )


def _format_commitment_confirmation(commitment: Commitment, now: datetime) -> str | None:
    values: list[str] = []
    if commitment.task_deadline:
        values.append(f"задача в {format_due_at(commitment.task_deadline, now)}")
    if commitment.report_time:
        values.append(f"отчёт в {format_due_at(commitment.report_time, now)}")
    if commitment.reminder_time:
        values.append(f"напоминание в {format_due_at(commitment.reminder_time, now)}")
    if commitment.unavailable_after:
        values.append(f"пауза с {format_due_at(commitment.unavailable_after, now)}")

    if not values:
        return None
    return f"Фиксирую: {', '.join(values)}, старое время отменяем."


def build_due_event_message(task_text: str, event_type: str) -> str:
    if event_type == "reminder":
        return (
            f"Напоминание. {task_text} ещё не финал, но уже пора выходить из кустов. "
            "Собери первый шаг и не торгуйся с диваном. ⛓️"
        )
    return build_reminder_message(task_text)


def run_reminder_loop(api: TelegramAPI, store: MemoryStore) -> None:
    timezone = get_timezone(AppConfig.timezone)
    while True:
        try:
            now = datetime.now(timezone)
            for reminder in store.due_reminders(now, limit=10):
                api.send_guarded_message(
                    reminder.chat_id,
                    build_due_event_message(reminder.task_text, reminder.event_type),
                )
                store.mark_reminder_sent(reminder.id)
        except Exception as exc:
            print(f"Reminder loop failed: {type(exc).__name__}: {exc}")
        time.sleep(AppConfig.reminder_check_interval)


def start_reminder_worker(api: TelegramAPI, store: MemoryStore) -> None:
    thread = threading.Thread(target=run_reminder_loop, args=(api, store), daemon=True)
    thread.start()


def run_telegram_bot() -> None:
    if not AppConfig.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required.")

    api = TelegramAPI(AppConfig.telegram_bot_token)
    store = create_memory_store()
    runtime_state = TelegramRuntimeState()
    offset: int | None = None

    run_startup_action("deleteWebhook", api.delete_webhook)
    run_startup_action("setMyCommands", api.set_commands)
    start_reminder_worker(api, store)
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
                recent_messages = tuple(store.recent_messages(chat_id, limit=5))
                api.send_guarded_message(
                    chat_id,
                    build_reply(chat_id, text, store, runtime_state),
                    recent_messages=recent_messages,
                )
        except RuntimeError as exc:
            print(exc)
            time.sleep(5)


if __name__ == "__main__":
    run_telegram_bot()
