from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
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
    from .work_blocks import create_work_block, match_work_block
    from .support import choose_content, next_run, parse_schedule
    from .feedback import feedback_preview
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
    from work_blocks import create_work_block, match_work_block
    from support import choose_content, next_run, parse_schedule
    from feedback import feedback_preview


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
BUTTON_FEEDBACK = "Мои наблюдения по проекту"
BUTTON_OBSERVATION = "📝 Поделиться наблюдением"
BUTTON_NEED_HELP = "🛟 Нужна помощь"
BUTTON_SUPPORT_NOW = "🤝 Поддержи сейчас"
BUTTON_SCHEDULE = "🕒 Расписание"
BUTTON_PAUSE = "⏸ Тишина до завтра"
BUTTON_COACH = "🔥 Тренер"

SUPPORT_KEYBOARD = {"keyboard": [
    [{"text": BUTTON_SUPPORT_NOW}, {"text": BUTTON_SCHEDULE}],
    [{"text": BUTTON_FEEDBACK}],
    [{"text": BUTTON_PAUSE}, {"text": BUTTON_COACH}],
], "resize_keyboard": True, "one_time_keyboard": False, "is_persistent": True}

MAIN_KEYBOARD = {
    "keyboard": [
        [{"text": BUTTON_SET_GOAL}, {"text": BUTTON_REPORT}],
        [{"text": BUTTON_KICK}, {"text": BUTTON_HELP}],
        [{"text": BUTTON_CLEAR_GOAL}],
        [{"text": BUTTON_FEEDBACK}],
    ],
    "resize_keyboard": True,
    "one_time_keyboard": False,
    "is_persistent": True,
}

FEEDBACK_KEYBOARD = {
    "keyboard": [
        [{"text": BUTTON_OBSERVATION}, {"text": BUTTON_NEED_HELP}],
        [{"text": "Отмена"}],
    ],
    "resize_keyboard": True,
    "one_time_keyboard": False,
    "is_persistent": False,
}

BOT_COMMANDS = (
    {"command": "start", "description": "Запустить НеНойBot"},
    {"command": "goal", "description": "Задать цель: /goal результат + срок"},
    {"command": "clear_goal", "description": "Сбросить цель"},
    {"command": "help", "description": "Показать команды"},
    {"command": "support", "description": "Режим поддержки"},
    {"command": "coach", "description": "Режим тренера"},
    {"command": "feedback", "description": "Наблюдения по НеНойBot"},
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

WORK_OVERVIEW_MARKERS = (
    "что у меня в работе",
    "что сейчас в работе",
    "какие задачи",
    "что по целям",
    "что у меня по целям",
    "что делать дальше",
)


@dataclass
class TelegramRuntimeState:
    awaiting_goal: set[int] = field(default_factory=set)
    awaiting_report: set[int] = field(default_factory=set)
    feedback_category: dict[int, str] = field(default_factory=dict)
    feedback_drafts: dict[int, str] = field(default_factory=dict)

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
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {"error_code": exc.code, "description": raw}
            raise TelegramDeliveryError.from_response(data) from exc
        except error.URLError as exc:
            raise TelegramDeliveryError("uncertain", f"Telegram request failed: {exc}") from exc

        if not data.get("ok"):
            raise TelegramDeliveryError.from_response(data)

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

    def _send_raw_message(self, chat_id: int, text: str, keyboard=None) -> int | None:
        result = self.request(
            "sendMessage",
            {
                "chat_id": str(chat_id),
                "text": text,
                "reply_markup": json.dumps(keyboard or MAIN_KEYBOARD, ensure_ascii=False),
            },
        )
        return result.get("message_id") if isinstance(result, dict) else None

    def send_guarded_message(
        self,
        chat_id: int,
        text: str,
        recent_messages: tuple[tuple[str, str], ...] = (),
        mode: str = "coach",
        keyboard=None,
    ) -> int | None:
        guarded_text = text if mode == "support" else prepare_outgoing_text(chat_id, text, recent_messages=recent_messages)
        return self._send_raw_message(
            chat_id,
            guarded_text,
            keyboard or (SUPPORT_KEYBOARD if mode == "support" else MAIN_KEYBOARD),
        )

    def send_message(self, chat_id: int, text: str) -> None:
        self.send_guarded_message(chat_id, text)


class TelegramDeliveryError(RuntimeError):
    def __init__(self, outcome: str, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.outcome = outcome
        self.retry_after = retry_after

    @classmethod
    def from_response(cls, data: dict[str, Any]) -> "TelegramDeliveryError":
        code = data.get("error_code")
        description = str(data.get("description", "Telegram API error"))
        retry_after = (data.get("parameters") or {}).get("retry_after")
        if code == 429:
            return cls("rate_limited", description, int(retry_after or 1))
        if code == 403:
            return cls("blocked", description)
        return cls("rejected", description)


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


def requires_private_chat(chat_type: str | None, chat_id: int, text: str, store: MemoryStore) -> bool:
    if chat_type == "private":
        return False
    return (
        text in {BUTTON_FEEDBACK, BUTTON_OBSERVATION, BUTTON_NEED_HELP}
        or text.startswith(("/support", "/feedback"))
        or store.get_feedback_draft(chat_id) is not None
        or store.get_support_settings(chat_id).mode == "support"
    )


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


def is_work_overview_request(text: str) -> bool:
    normalized = text.casefold()
    return any(marker in normalized for marker in WORK_OVERVIEW_MARKERS)


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

    # Commands/navigation always win over transient coach and feedback states.
    if text.startswith("/cancel") or text == "Отмена":
        runtime_state.clear(chat_id)
        runtime_state.feedback_category.pop(chat_id, None)
        runtime_state.feedback_drafts.pop(chat_id, None)
        store.delete_feedback_draft(chat_id)
        return "Отменено. Сообщение команде не отправлено."

    if text.startswith("/support") and not text.startswith(("/support_now", "/support_schedule", "/support_pause", "/support_resume", "/support_off")):
        runtime_state.clear(chat_id)
        store.set_mode(chat_id, "support")
        return ("Здесь не нужно сдавать экзамен на продуктивность. Будем возвращаться к интересу: "
                "пробовать, разбираться и общаться без лишнего напряжения. Можно говорить с ботом "
                "или включить короткие напоминания в течение дня.")
    if text.startswith("/coach") or text == BUTTON_COACH:
        runtime_state.clear(chat_id)
        store.set_mode(chat_id, "coach")
        return "Режим тренера включён. Сохранённые цель и история на месте."

    settings = store.get_support_settings(chat_id)
    if text in {BUTTON_FEEDBACK, "/feedback", "📝 Мои наблюдения"}:
        runtime_state.clear(chat_id)
        if not AppConfig.care_chat_id:
            return "Служба заботы сейчас не настроена, поэтому я не приму сообщение в никуда."
        store.save_feedback_draft(chat_id, None, None)
        return ("Выбери тип сообщения:\n"
                f"{BUTTON_OBSERVATION} — впечатления и идеи о проекте.\n"
                f"{BUTTON_NEED_HELP} — обращение в службу заботы.\n\n"
                "Обычная переписка автоматически не передаётся. /cancel — отмена.")
    draft = store.get_feedback_draft(chat_id)
    if text in {BUTTON_OBSERVATION, BUTTON_NEED_HELP} and draft is not None:
        category = "observation" if text == BUTTON_OBSERVATION else "help"
        store.save_feedback_draft(chat_id, category, None)
        return ("Напиши текст до 3000 символов. Его прочитает команда бота; "
                "обычная переписка автоматически не передаётся. /cancel — отмена.")
    if draft is not None and draft.category and draft.body is None:
        if len(text) > 3000:
            return "Текст длиннее 3000 символов. Сократи его; черновик не отправлен."
        store.save_feedback_draft(chat_id, draft.category, text)
        return feedback_preview(draft.category, text) + "\n\n📨 Отправить / ✏️ Изменить / Отмена"
    if text in {"✏️ Изменить", "/feedback_edit"} and draft is not None and draft.body:
        store.save_feedback_draft(chat_id, draft.category, None)
        return "Пришли исправленный текст."
    if text in {"📨 Отправить", "/feedback_send"} and draft is not None and draft.body:
        try:
            feedback = store.create_feedback(chat_id, draft.category or "observation", draft.body, settings.mode, None)
        except ValueError:
            return "Лимит — 5 новых обращений в час. Черновик сохранён на 24 часа; попробуй позже."
        store.delete_feedback_draft(chat_id)
        return f"Обращение №{feedback.id} сохранено. Уведомление команде ожидает подтверждения доставки."

    if text.startswith("/support_schedule") or text == BUTTON_SCHEDULE:
        value = text.removeprefix("/support_schedule").strip()
        if not value:
            run = next_run(settings, datetime.now(UTC))
            return (f"Расписание: {', '.join(settings.times)}; timezone: {settings.timezone or 'не выбран'}; "
                    f"статус: {'включено' if settings.enabled else 'выключено'}; следующий запуск: {run or 'нет'}.\n"
                    "Изменить: /support_schedule 09:00,13:00,17:00 Europe/Moscow")
        try:
            times, timezone_name = parse_schedule(value)
        except ValueError as exc:
            return str(exc)
        store.configure_support(chat_id, times, timezone_name)
        return f"Рассылка включена ежедневно: {', '.join(times)} ({timezone_name})."
    if text.startswith("/support_off"):
        store.disable_support(chat_id)
        return "Рассылка выключена. Режим и сохранённые часы не удалены."
    if text.startswith("/support_pause") or text == BUTTON_PAUSE:
        if not settings.timezone:
            return "Сначала настрой timezone: /support_schedule 09:00,13:00,17:00 Europe/Moscow"
        local_tomorrow = datetime.now(UTC).astimezone(__import__('zoneinfo').ZoneInfo(settings.timezone)).date() + timedelta(days=1)
        store.pause_support(chat_id, local_tomorrow)
        return "Тишина до завтра. Рассылка продолжится со следующего штатного слота."
    if text.startswith("/support_resume"):
        store.pause_support(chat_id, None)
        return "Рассылка возобновлена со следующего будущего слота."
    if text.startswith("/support_now") or text == BUTTON_SUPPORT_NOW:
        return choose_content(set(), f"manual:{chat_id}:{datetime.now(UTC).date()}")["text"]

    if settings.mode == "support":
        context = ConversationContext(None, store.get_summary(chat_id), tuple(store.recent_messages(chat_id, 8)))
        reply = generate_ai_response(text, context, mode="support")
        store.append_message(chat_id, "user:support", text)
        store.append_message(chat_id, "assistant:support", reply)
        return reply

    reminder_source_text = text

    if is_meta_complaint(text):
        runtime_state.clear(chat_id)
        store.append_message(chat_id, "user", text)
        store.append_message(chat_id, "assistant", META_COMPLAINT_REPLY)
        return META_COMPLAINT_REPLY

    if is_work_overview_request(text):
        runtime_state.clear(chat_id)
        response = build_work_overview(chat_id, store)
        store.append_message(chat_id, "user", text)
        store.append_message(chat_id, "assistant", response)
        return response

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

    work_block_id = resolve_work_block_id(chat_id, commitment, store)

    if commitment.reminder_time:
        store.add_reminder(
            chat_id,
            commitment.task_text,
            commitment.reminder_time,
            reminder_type="reminder",
            work_block_id=work_block_id,
        )
    if commitment.active_checkin_time:
        store.add_reminder(
            chat_id,
            commitment.task_text,
            commitment.active_checkin_time,
            reminder_type="checkin",
            work_block_id=work_block_id,
        )

    confirmation = _format_commitment_confirmation(commitment, now)
    if confirmation is None:
        return None

    return (
        f"{confirmation} "
        "Это не дата в заметках. Это проверка факта. "
        "Вернешься с результатом или с новой отговоркой? 🔥"
    )


def resolve_work_block_id(chat_id: int, commitment: Commitment, store: MemoryStore) -> str | None:
    try:
        blocks = store.list_active_work_blocks(chat_id)
        match = match_work_block(commitment.task_text, blocks)
        if match.block is not None:
            return match.block.id
        return store.upsert_work_block(create_work_block(chat_id, commitment.task_text)).id
    except Exception as exc:
        logger.warning("Work block assignment skipped chat_id=%s: %s", chat_id, exc)
        return None


def build_work_overview(chat_id: int, store: MemoryStore) -> str:
    pending_events = store.pending_reminders(chat_id, limit=50)
    if not pending_events:
        return (
            "Связь есть. Совесть тоже на линии, не радуйся.\n\n"
            "В работе пусто: либо всё закрыто, либо задачи прячутся без срока.\n\n"
            "Назови следующий фронт и время. Табло любит факты. ⛓️"
        )

    grouped: dict[str, list] = {}
    for event in pending_events:
        group_key = event.work_block_id or f"event:{event.id}"
        grouped.setdefault(group_key, []).append(event)

    block_rows = []
    for group_key, events in grouped.items():
        selected_event = _select_overview_event(events)
        block_title = _work_block_title(chat_id, group_key, store)
        block_rows.append((block_title, selected_event))

    block_rows.sort(key=lambda item: item[1].due_at)
    visible_rows = block_rows[:3]
    fronts_label = _fronts_label(len(visible_rows))

    lines = [
        "Связь есть. Совесть тоже на линии, не радуйся.",
        "",
        f"В работе {fronts_label}:",
    ]
    for index, (title, event) in enumerate(visible_rows, start=1):
        lines.append(
            f"{index}. {title} — до {event.due_at:%H:%M}: {_compact_task_text(event.task_text)}."
        )

    first_title = visible_rows[0][0]
    lines.extend(
        [
            "",
            f"Сначала режешь ближайший дедлайн — {first_title}. "
            "Остальное не трогаем, пока табло не получит факт. Погнали. ⛓️",
        ]
    )
    return "\n".join(lines)


def _select_overview_event(events: list) -> object:
    checkins = [event for event in events if event.event_type == "checkin"]
    candidates = checkins or events
    return sorted(candidates, key=lambda event: event.due_at)[0]


def _work_block_title(chat_id: int, group_key: str, store: MemoryStore) -> str:
    if group_key.startswith("event:"):
        return "Рабочий блок"
    block = store.get_work_block(chat_id, group_key)
    return block.title if block is not None else "Рабочий блок"


def _fronts_label(count: int) -> str:
    if count == 1:
        return "один фронт"
    if count == 2:
        return "два фронта"
    return "три фронта"


def _compact_task_text(task_text: str) -> str:
    compact = " ".join(task_text.split()).strip(" .,:;—-")
    if len(compact) <= 90:
        return compact
    return f"{compact[:87].rstrip()}..."


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


def deliver_telegram(send, sleep_fn=time.sleep) -> tuple[str, int | None]:
    """Send once, retrying only Telegram's explicit definitely-undelivered 429."""
    try:
        return "confirmed", send()
    except TelegramDeliveryError as exc:
        if exc.outcome != "rate_limited":
            return exc.outcome, None
        sleep_fn(max(1, exc.retry_after or 1))
        try:
            return "confirmed", send()
        except TelegramDeliveryError as retry_exc:
            return retry_exc.outcome, None
    except RuntimeError:
        # Compatibility for custom API adapters: the network boundary is ambiguous.
        return "uncertain", None


def run_reminder_loop(api: TelegramAPI, store: MemoryStore) -> None:
    timezone = get_timezone(AppConfig.timezone)
    while True:
        try:
            now = datetime.now(timezone)
            for reminder in store.due_reminders(now, limit=10):
                if store.get_support_settings(reminder.chat_id).mode != "coach":
                    store.mark_reminder_sent(reminder.id)
                    continue
                api.send_guarded_message(
                    reminder.chat_id,
                    build_due_event_message(reminder.task_text, reminder.event_type),
                )
                store.mark_reminder_sent(reminder.id)
            for slot in store.claim_support_slots(datetime.now(UTC), limit=50):
                # Re-check immediately before the network boundary.
                current = store.get_support_settings(slot.chat_id)
                if current.mode != "support" or not current.enabled:
                    store.complete_support_slot(slot, "cancelled")
                    continue
                status, message_id = deliver_telegram(
                    lambda: api.send_guarded_message(slot.chat_id, slot.text, mode="support")
                )
                store.complete_support_slot(slot, status, message_id)
            deliver_pending_feedback(api, store)
        except Exception as exc:
            print(f"Reminder loop failed: {type(exc).__name__}: {exc}")
        time.sleep(AppConfig.reminder_check_interval)


def start_reminder_worker(api: TelegramAPI, store: MemoryStore) -> None:
    thread = threading.Thread(target=run_reminder_loop, args=(api, store), daemon=True)
    thread.start()


def deliver_pending_feedback(api: TelegramAPI, store: MemoryStore) -> None:
    """Deliver queued care notices and acknowledge only confirmed deliveries."""
    if not AppConfig.care_chat_id:
        return
    for feedback in store.pending_feedback():
        notice = (f"Обращение №{feedback.id}\nТип: {feedback.category}\n"
                  f"Время: {feedback.created_at.isoformat()}\nРежим: {feedback.mode_at_submit}\n\n{feedback.body}")
        status, message_id = deliver_telegram(
            lambda: api._send_raw_message(AppConfig.care_chat_id, notice)
        )
        store.mark_feedback_notification(feedback.id, status, message_id)
        if status == "confirmed":
            # This second delivery has its own outcome: never turn a failed acknowledgement
            # into a false statement about the care notification itself.
            deliver_telegram(lambda: api._send_raw_message(
                feedback.chat_id,
                f"Обращение №{feedback.id} передано команде. Ответ появится здесь.",
            ))


def deliver_care_reply(api: TelegramAPI, store: MemoryStore, feedback, body: str) -> str:
    status, _ = deliver_telegram(lambda: api._send_raw_message(
        feedback.chat_id, f"🛟 Ответ службы заботы по обращению №{feedback.id}\n\n{body}"
    ))
    if status == "confirmed":
        store.mark_feedback_replied(feedback.id)
    return status


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
                message = update.get("message") or update.get("edited_message") or {}
                chat_type = (message.get("chat") or {}).get("type")
                from_id = (message.get("from") or {}).get("id")
                if text.startswith("/care_reply"):
                    if chat_id != AppConfig.care_chat_id or from_id not in AppConfig.care_admin_ids:
                        api._send_raw_message(chat_id, "Команда недоступна.")
                        continue
                    parts = text.split(maxsplit=2)
                    if len(parts) != 3 or not parts[1].isdigit():
                        api._send_raw_message(chat_id, "Формат: /care_reply <feedback_id> <текст>")
                        continue
                    feedback = store.get_feedback(int(parts[1]))
                    if not feedback:
                        api._send_raw_message(chat_id, "Обращение не найдено.")
                        continue
                    status = deliver_care_reply(api, store, feedback, parts[2])
                    if status == "confirmed":
                        api._send_raw_message(chat_id, "Ответ доставлен.")
                    elif status == "blocked":
                        api._send_raw_message(chat_id, "Пользователь заблокировал бота (Telegram 403). Ответ не доставлен.")
                    elif status == "rate_limited":
                        api._send_raw_message(chat_id, "Telegram всё ещё ограничивает отправку (429). Ответ не доставлен.")
                    elif status == "rejected":
                        api._send_raw_message(chat_id, "Telegram отклонил ответ. Ответ не доставлен.")
                    else:
                        api._send_raw_message(chat_id, "Исход доставки неоднозначен; повтор вручную может создать дубль.")
                    continue
                if requires_private_chat(chat_type, chat_id, text, store):
                    api._send_raw_message(chat_id, "Персональные режимы доступны только в личном чате с ботом.")
                    continue
                recent_messages = tuple(store.recent_messages(chat_id, limit=5))
                reply = build_reply(chat_id, text, store, runtime_state)
                mode = store.get_support_settings(chat_id).mode
                keyboard = FEEDBACK_KEYBOARD if store.get_feedback_draft(chat_id) is not None else None
                api.send_guarded_message(
                    chat_id,
                    reply,
                    recent_messages=recent_messages,
                    mode=mode,
                    keyboard=keyboard,
                )
        except RuntimeError as exc:
            print(exc)
            time.sleep(5)


if __name__ == "__main__":
    run_telegram_bot()
