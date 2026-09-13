from __future__ import annotations

from . import care_runtime as runtime
from . import asset_wrapper as app
from . import telegram_bot as base


LEGACY_BUTTON_FEEDBACK = base.BUTTON_FEEDBACK
BUTTON_WRITE_TEAM = "💬 Написать команде"
BUTTON_OBSERVATION_PROJECT = "💡 Идея или отзыв о проекте"
BUTTON_NEED_HELP_TEAM = "🛟 Нужна помощь команды"

LIGHTNESS_MODE_INTRO = (
    "🌿 Лёгкость включена.\n\n"
    "Просто пиши сюда как в обычный чат — я отвечу в режиме «Лёгкость».\n\n"
    "Кнопки снизу:\n"
    "🤝 Поддержи сейчас — пришлю одну фразу и изображение\n"
    "🕒 Расписание — настроить ежедневные сообщения\n"
    "💬 Написать команде — отзыв, идея или помощь живого человека\n"
    "⏸ Тишина до завтра — поставить рассылку на паузу\n"
    "🎛 Режим — переключить режим"
)

TRAINER_MODE_INTRO = (
    "🔥 Тренер включён.\n\n"
    "Просто пиши сюда как в обычный чат — я отвечу в режиме «Тренер».\n\n"
    "Кнопки снизу:\n"
    "🎯 Задать цель — зафиксировать цель\n"
    "✅ Отчёт — сообщить результат\n"
    "🔥 Пинок — если застрял и нужен жёсткий толчок\n"
    "📌 Меню — подсказка по командам\n"
    "🧹 Сбросить цель — очистить текущую цель\n"
    "💬 Написать команде — отзыв, идея или помощь живого человека\n"
    "🎛 Режим — переключить режим"
)

CARE_INTRO = (
    "💬 Это связь с командой НеНойBot.\n\n"
    "Сообщение отсюда увидят живые люди, а не AI-чат. Обычная переписка с ботом автоматически не передаётся.\n\n"
    "Что хочешь передать?\n"
    "💡 Идея или отзыв о проекте — что заметил, что понравилось или что стоит улучшить.\n"
    "🛟 Нужна помощь команды — если нужен ответ живого человека.\n\n"
    "Если хотел поговорить с ботом — нажми «Отмена» и просто напиши сообщение в чат."
)

# User-facing wording: human-team contact must look different from ordinary AI chat.
base.BUTTON_FEEDBACK = BUTTON_WRITE_TEAM
base.BUTTON_OBSERVATION = BUTTON_OBSERVATION_PROJECT
base.BUTTON_NEED_HELP = BUTTON_NEED_HELP_TEAM

# Rebuild persistent keyboards because asset_wrapper created them before this UX layer loaded.
base.MAIN_KEYBOARD = {
    "keyboard": [
        [{"text": base.BUTTON_SET_GOAL}, {"text": base.BUTTON_REPORT}],
        [{"text": base.BUTTON_KICK}, {"text": base.BUTTON_HELP}],
        [{"text": base.BUTTON_CLEAR_GOAL}],
        [{"text": app.BUTTON_MODE}, {"text": BUTTON_WRITE_TEAM}],
    ],
    "resize_keyboard": True,
    "one_time_keyboard": False,
    "is_persistent": True,
}

base.SUPPORT_KEYBOARD = {
    "keyboard": [
        [{"text": base.BUTTON_SUPPORT_NOW}, {"text": base.BUTTON_SCHEDULE}],
        [{"text": BUTTON_WRITE_TEAM}],
        [{"text": base.BUTTON_PAUSE}, {"text": app.BUTTON_MODE}],
    ],
    "resize_keyboard": True,
    "one_time_keyboard": False,
    "is_persistent": True,
}

base.FEEDBACK_KEYBOARD = {
    "keyboard": [
        [{"text": BUTTON_OBSERVATION_PROJECT}, {"text": BUTTON_NEED_HELP_TEAM}],
        [{"text": "Отмена"}],
    ],
    "resize_keyboard": True,
    "one_time_keyboard": False,
    "is_persistent": False,
}

# The command menu is also user-facing; make the human-team boundary explicit there.
base.BOT_COMMANDS = tuple(
    {**command, "description": "Написать команде НеНойBot"}
    if command.get("command") == "feedback"
    else command
    for command in base.BOT_COMMANDS
)

_original_build_reply = base.build_reply


def _is_lightness_switch(text: str) -> bool:
    stripped = text.strip()
    return stripped == app.BUTTON_LIGHTNESS or (
        stripped.startswith("/support")
        and not stripped.startswith(
            ("/support_now", "/support_schedule", "/support_pause", "/support_resume", "/support_off")
        )
    )


def _is_trainer_switch(text: str) -> bool:
    stripped = text.strip()
    return stripped == base.BUTTON_COACH or stripped.startswith("/coach")


def build_reply(chat_id: int, text: str, store, runtime_state=None) -> str:
    # Keep stale Telegram keyboards working after the button rename.
    normalized = BUTTON_WRITE_TEAM if text.strip() == LEGACY_BUTTON_FEEDBACK else text
    reply = _original_build_reply(chat_id, normalized, store, runtime_state)

    if _is_lightness_switch(normalized):
        return LIGHTNESS_MODE_INTRO
    if _is_trainer_switch(normalized):
        return TRAINER_MODE_INTRO

    if normalized.strip() in {BUTTON_WRITE_TEAM, "/feedback", "📝 Мои наблюдения"}:
        draft = store.get_feedback_draft(chat_id)
        if draft is not None and draft.category is None:
            return CARE_INTRO

    return reply


base.build_reply = build_reply


def run() -> None:
    runtime.run()


if __name__ == "__main__":
    run()
