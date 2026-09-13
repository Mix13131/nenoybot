from __future__ import annotations

from datetime import UTC, datetime, timedelta

from . import care_ack_runtime as runtime
from . import care_runtime as care
from . import telegram_bot as base
from .config import AppConfig
from .user_registry import UserRegistry


FOLLOWUP_WINDOW = timedelta(minutes=30)
USERS = UserRegistry(AppConfig.database_url, AppConfig.timezone)
_original_extract_text_message = base.extract_text_message
_original_build_reply = base.build_reply

_NAVIGATION_TEXTS = {
    base.BUTTON_SET_GOAL,
    base.BUTTON_REPORT,
    base.BUTTON_KICK,
    base.BUTTON_HELP,
    base.BUTTON_CLEAR_GOAL,
    base.BUTTON_FEEDBACK,
    base.BUTTON_OBSERVATION,
    base.BUTTON_NEED_HELP,
    base.BUTTON_SUPPORT_NOW,
    base.BUTTON_SCHEDULE,
    base.BUTTON_PAUSE,
    base.BUTTON_COACH,
    base.BUTTON_FEEDBACK_SEND,
    base.BUTTON_FEEDBACK_EDIT,
    # Keep the old persistent Telegram keyboard safe during the rollout.
    "Мои наблюдения по проекту",
    "Отмена",
    "🎛 Режим",
    "🌿 Лёгкость",
    "↩️ В режим",
    "✅ Включить 09·13·17",
    "✏️ Изменить время",
    "🚫 Выключить",
}


def _active_feedback_id(chat_id: int) -> int | None:
    if not AppConfig.database_url:
        return None
    import psycopg

    with psycopg.connect(AppConfig.database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT cl.feedback_id, cl.created_at
            FROM nenoy_care_links cl
            JOIN nenoy_feedback f ON f.id = cl.feedback_id
            WHERE f.chat_id = %s
              AND cl.direction = 'care_to_user'
            ORDER BY cl.id DESC
            LIMIT 1
            """,
            (chat_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        feedback_id = int(row[0])
        care_reply_at = row[1]
        if care_reply_at is None or care_reply_at < datetime.now(UTC) - FOLLOWUP_WINDOW:
            return None

        cursor.execute(
            """
            SELECT 1
            FROM nenoy_care_links
            WHERE feedback_id = %s
              AND direction = 'user_to_care'
              AND created_at > %s
            LIMIT 1
            """,
            (feedback_id, care_reply_at),
        )
        if cursor.fetchone():
            return None
        return feedback_id


def _is_private(update) -> bool:
    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return False
    chat = message.get("chat")
    return isinstance(chat, dict) and chat.get("type") == "private"


def _track_user(update) -> None:
    try:
        USERS.track_private_message(update)
    except Exception as exc:
        print(f"User registry tracking skipped: {type(exc).__name__}: {exc}")


def extract_text_message(update):
    _track_user(update)
    result = _original_extract_text_message(update)
    if result is None:
        return None

    chat_id, text = result
    if text == care.CARE_USER_REPLY_SENTINEL:
        return result
    if not _is_private(update):
        return result
    if text.startswith("/") or text in _NAVIGATION_TEXTS:
        return result

    feedback_id = _active_feedback_id(chat_id)
    if feedback_id is None:
        return result

    care._PENDING_USER_REPLY[chat_id] = (feedback_id, text)
    return chat_id, care.CARE_USER_REPLY_SENTINEL


def _command_name(text: str) -> str:
    head = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    return head.split("@", 1)[0]


def _users_limit(text: str) -> int:
    parts = text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return 10
    try:
        return max(1, min(int(parts[1]), 15))
    except ValueError:
        return 10


def build_reply(chat_id: int, text: str, store, runtime_state=None) -> str:
    command = _command_name(text)
    if command in {"/users", "/stats"}:
        if chat_id not in AppConfig.care_admin_ids:
            return "Команда недоступна."
        if command == "/stats":
            return USERS.stats_text()
        return USERS.users_text(_users_limit(text))
    return _original_build_reply(chat_id, text, store, runtime_state)


base.extract_text_message = extract_text_message
care.extract_text_message = extract_text_message
base.build_reply = build_reply
care.build_reply = build_reply
runtime.build_reply = build_reply


def run() -> None:
    runtime.run()


if __name__ == "__main__":
    run()
