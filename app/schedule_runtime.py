from __future__ import annotations

from . import asset_runtime as runtime
from . import asset_wrapper as app
from . import telegram_bot as base
from .config import AppConfig
from .support import DEFAULT_TIMES, parse_schedule


BUTTON_ENABLE_DEFAULT = "✅ Включить 09·13·17"
BUTTON_EDIT_TIMES = "✏️ Изменить время"
BUTTON_DISABLE = "🚫 Выключить"
BUTTON_BACK_TO_SUPPORT = "↩️ В режим"
SCHEDULE_MENU_ENABLED_PREFIX = "__schedule_menu_enabled__:"
SCHEDULE_MENU_DISABLED_PREFIX = "__schedule_menu_disabled__:"
SCHEDULE_EDIT_PREFIX = "__schedule_edit__:"
SCHEDULE_RESULT_PREFIX = "__schedule_result__:"

_PENDING_EDIT: set[int] = set()

SCHEDULE_KEYBOARD_DISABLED = {
    "keyboard": [
        [{"text": BUTTON_ENABLE_DEFAULT}, {"text": BUTTON_EDIT_TIMES}],
        [{"text": BUTTON_BACK_TO_SUPPORT}],
    ],
    "resize_keyboard": True,
    "one_time_keyboard": False,
    "is_persistent": True,
}

SCHEDULE_KEYBOARD_ENABLED = {
    "keyboard": [
        [{"text": BUTTON_EDIT_TIMES}, {"text": BUTTON_DISABLE}],
        [{"text": BUTTON_BACK_TO_SUPPORT}],
    ],
    "resize_keyboard": True,
    "one_time_keyboard": False,
    "is_persistent": True,
}

SCHEDULE_EDIT_KEYBOARD = {
    "keyboard": [[{"text": BUTTON_BACK_TO_SUPPORT}]],
    "resize_keyboard": True,
    "one_time_keyboard": False,
    "is_persistent": True,
}

_original_build_reply = app.build_reply
_original_send_guarded_message = app.send_guarded_message


def _timezone_for(settings) -> str:
    return settings.timezone or AppConfig.timezone or "Europe/Moscow"


def _menu_text(settings) -> str:
    status = "включены ✅" if settings.enabled else "выключены"
    times = " · ".join(settings.times or DEFAULT_TIMES)
    timezone = _timezone_for(settings)
    body = "\n".join([
        "🕒 Напоминания режима «Лёгкость»",
        f"Статус: {status}",
        f"Время: {times}",
        f"Часовой пояс: {timezone}",
        "",
        (
            "Можно изменить часы или выключить напоминания."
            if settings.enabled
            else "Можно включить стандартное расписание или задать свои часы."
        ),
    ])
    prefix = SCHEDULE_MENU_ENABLED_PREFIX if settings.enabled else SCHEDULE_MENU_DISABLED_PREFIX
    return prefix + body


def _result_text(message: str) -> str:
    return SCHEDULE_RESULT_PREFIX + message


def build_reply(chat_id: int, text: str, store, runtime_state=None) -> str:
    stripped = text.strip()

    if stripped == base.BUTTON_SCHEDULE or stripped == "/support_schedule":
        _PENDING_EDIT.discard(chat_id)
        return _menu_text(store.get_support_settings(chat_id))

    if stripped == BUTTON_ENABLE_DEFAULT:
        _PENDING_EDIT.discard(chat_id)
        settings = store.get_support_settings(chat_id)
        timezone = _timezone_for(settings)
        store.configure_support(chat_id, DEFAULT_TIMES, timezone)
        return _result_text(
            "✅ Напоминания включены: 09:00 · 13:00 · 17:00.\n"
            "Вернул тебя в режим «Лёгкость»."
        )

    if stripped == BUTTON_EDIT_TIMES:
        _PENDING_EDIT.add(chat_id)
        settings = store.get_support_settings(chat_id)
        timezone = _timezone_for(settings)
        return (
            SCHEDULE_EDIT_PREFIX
            + "✏️ Напиши от одного до трёх времён через запятую.\n"
            + "Например: 09:30, 14:00, 19:30\n\n"
            + f"Часовой пояс оставлю: {timezone}."
        )

    if stripped == BUTTON_DISABLE:
        _PENDING_EDIT.discard(chat_id)
        store.disable_support(chat_id)
        return _result_text(
            "🚫 Напоминания выключены.\n"
            "Вернул тебя в режим «Лёгкость»."
        )

    if stripped == BUTTON_BACK_TO_SUPPORT:
        _PENDING_EDIT.discard(chat_id)
        return _result_text("🌿 Вернулись в режим «Лёгкость».")

    if chat_id in _PENDING_EDIT and not stripped.startswith("/"):
        settings = store.get_support_settings(chat_id)
        timezone = _timezone_for(settings)
        compact_times = "".join(stripped.split())
        try:
            times, _ = parse_schedule(f"{compact_times} {timezone}")
        except ValueError:
            return (
                SCHEDULE_EDIT_PREFIX
                + "Не понял время. Напиши, например: 09:30, 14:00, 19:30\n"
                + "Можно указать от одного до трёх времён."
            )
        store.configure_support(chat_id, times, timezone)
        _PENDING_EDIT.discard(chat_id)
        return _result_text(
            f"✅ Новое расписание сохранено: {' · '.join(times)}.\n"
            "Вернул тебя в режим «Лёгкость»."
        )

    return _original_build_reply(chat_id, text, store, runtime_state)


def send_guarded_message(
    api: base.TelegramAPI,
    chat_id: int,
    text: str,
    recent_messages=(),
    mode: str = "coach",
    keyboard=None,
) -> int | None:
    if text.startswith(SCHEDULE_MENU_ENABLED_PREFIX):
        body = text.removeprefix(SCHEDULE_MENU_ENABLED_PREFIX)
        return api._send_raw_message(chat_id, body, SCHEDULE_KEYBOARD_ENABLED)
    if text.startswith(SCHEDULE_MENU_DISABLED_PREFIX):
        body = text.removeprefix(SCHEDULE_MENU_DISABLED_PREFIX)
        return api._send_raw_message(chat_id, body, SCHEDULE_KEYBOARD_DISABLED)
    if text.startswith(SCHEDULE_EDIT_PREFIX):
        body = text.removeprefix(SCHEDULE_EDIT_PREFIX)
        return api._send_raw_message(chat_id, body, SCHEDULE_EDIT_KEYBOARD)
    if text.startswith(SCHEDULE_RESULT_PREFIX):
        body = text.removeprefix(SCHEDULE_RESULT_PREFIX)
        return api._send_raw_message(chat_id, body, base.SUPPORT_KEYBOARD)
    return _original_send_guarded_message(
        api,
        chat_id,
        text,
        recent_messages=recent_messages,
        mode=mode,
        keyboard=keyboard,
    )


app.build_reply = build_reply
base.build_reply = build_reply
app.send_guarded_message = send_guarded_message
base.TelegramAPI.send_guarded_message = send_guarded_message


def run() -> None:
    runtime.run()


if __name__ == "__main__":
    run()
