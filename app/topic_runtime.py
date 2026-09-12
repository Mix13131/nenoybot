from __future__ import annotations

import logging

from . import ui_runtime as runtime
from . import care_runtime as care
from . import telegram_bot as base
from .config import AppConfig


logger = logging.getLogger(__name__)
_PENDING_GROUP_THREADS: dict[int, int | None] = {}


class CareTopicStore:
    def __init__(self, database_url: str | None) -> None:
        self.database_url = database_url
        self._memory: dict[int, int] = {}
        self.ensure_schema()

    def _connect(self):
        if not self.database_url:
            return None
        import psycopg
        return psycopg.connect(self.database_url, autocommit=True)

    def ensure_schema(self) -> None:
        if not self.database_url:
            return
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS nenoy_care_user_topics (
                    user_chat_id BIGINT PRIMARY KEY,
                    message_thread_id BIGINT NOT NULL,
                    topic_name TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

    def get(self, user_chat_id: int) -> int | None:
        if not self.database_url:
            return self._memory.get(user_chat_id)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT message_thread_id FROM nenoy_care_user_topics WHERE user_chat_id = %s",
                (user_chat_id,),
            )
            row = cursor.fetchone()
        return int(row[0]) if row else None

    def set(self, user_chat_id: int, message_thread_id: int, topic_name: str) -> None:
        if not self.database_url:
            self._memory[user_chat_id] = message_thread_id
            return
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO nenoy_care_user_topics
                    (user_chat_id, message_thread_id, topic_name, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (user_chat_id) DO UPDATE SET
                    message_thread_id = EXCLUDED.message_thread_id,
                    topic_name = EXCLUDED.topic_name,
                    updated_at = NOW()
                """,
                (user_chat_id, message_thread_id, topic_name),
            )


TOPICS = CareTopicStore(AppConfig.database_url)

_original_extract_text_message = base.extract_text_message
_original_send_guarded_message = base.TelegramAPI.send_guarded_message
_original_deliver_pending_feedback = base.deliver_pending_feedback


def _topic_name(api: base.TelegramAPI, user_chat_id: int) -> str:
    fallback = f"👤 Пользователь {user_chat_id}"
    try:
        chat = api.request("getChat", {"chat_id": str(user_chat_id)})
    except base.TelegramDeliveryError:
        return fallback[:128]
    if not isinstance(chat, dict):
        return fallback[:128]

    first_name = str(chat.get("first_name") or "").strip()
    last_name = str(chat.get("last_name") or "").strip()
    username = str(chat.get("username") or "").strip()
    display = " ".join(part for part in (first_name, last_name) if part)
    if username:
        display = f"{display} · @{username}" if display else f"@{username}"
    if not display:
        display = f"Пользователь {user_chat_id}"
    return f"👤 {display}"[:128]


def _ensure_topic(
    api: base.TelegramAPI,
    care_chat_id: int,
    user_chat_id: int,
    fallback_thread_id: int | None = None,
) -> int | None:
    existing = TOPICS.get(user_chat_id)
    if existing is not None:
        return existing

    name = _topic_name(api, user_chat_id)
    try:
        result = api.request(
            "createForumTopic",
            {
                "chat_id": str(care_chat_id),
                "name": name,
            },
        )
    except base.TelegramDeliveryError as exc:
        logger.warning(
            "Care topic creation failed user_chat_id=%s care_chat_id=%s: %s",
            user_chat_id,
            care_chat_id,
            exc,
        )
        return fallback_thread_id

    if not isinstance(result, dict) or not isinstance(result.get("message_thread_id"), int):
        logger.warning("Care topic creation returned invalid payload for user_chat_id=%s", user_chat_id)
        return fallback_thread_id

    thread_id = int(result["message_thread_id"])
    TOPICS.set(user_chat_id, thread_id, name)
    return thread_id


def extract_text_message(update):
    message = update.get("message") or update.get("edited_message")
    result = _original_extract_text_message(update)
    if result and result[1] == care.CARE_GROUP_REPLY_SENTINEL and isinstance(message, dict):
        thread_id = message.get("message_thread_id")
        _PENDING_GROUP_THREADS[result[0]] = int(thread_id) if isinstance(thread_id, int) else None
    return result


def _user_keyboard(user_chat_id: int):
    return (
        base.SUPPORT_KEYBOARD
        if care.CARE.get_user_mode(user_chat_id) == "support"
        else base.MAIN_KEYBOARD
    )


def send_guarded_message(
    api: base.TelegramAPI,
    chat_id: int,
    text: str,
    recent_messages=(),
    mode: str = "coach",
    keyboard=None,
) -> int | None:
    if text == care.CARE_GROUP_REPLY_SENTINEL:
        pending = care._PENDING_GROUP_REPLY.pop(chat_id, None)
        current_thread = _PENDING_GROUP_THREADS.pop(chat_id, None)
        config = care.CARE.get_config()
        if pending is None:
            return care._send_group_message(
                api,
                chat_id,
                "Не смог связать ответ с обращением.",
                message_thread_id=current_thread,
            )

        feedback_id, body = pending
        user_chat_id = care.CARE.get_user_chat_id(feedback_id)
        if user_chat_id is None:
            return care._send_group_message(
                api,
                chat_id,
                "Пользователь обращения не найден.",
                message_thread_id=current_thread,
            )

        status, user_message_id = base.deliver_telegram(
            lambda: api._send_raw_message(
                user_chat_id,
                (
                    f"🛟 Ответ службы заботы по обращению №{feedback_id}\n\n"
                    f"{body}\n\n"
                    "Можно ответить прямо на это сообщение — ответ вернётся в службу заботы."
                ),
                _user_keyboard(user_chat_id),
            )
        )
        if status == "confirmed" and user_message_id is not None:
            care.CARE.link(
                feedback_id,
                user_message_id=user_message_id,
                direction="care_to_user",
            )
            care.CARE.mark_replied(feedback_id)
            return care._send_group_message(
                api,
                chat_id,
                f"✅ Ответ доставлен пользователю по обращению №{feedback_id}.",
                message_thread_id=current_thread,
            )
        return care._send_group_message(
            api,
            chat_id,
            f"⚠️ Ответ по обращению №{feedback_id} не доставлен: {status}.",
            message_thread_id=current_thread,
        )

    if text == care.CARE_USER_REPLY_SENTINEL:
        pending = care._PENDING_USER_REPLY.pop(chat_id, None)
        if pending is None:
            return api._send_raw_message(chat_id, "Не смог связать ответ с обращением.")
        feedback_id, body = pending
        config = care.CARE.get_config()
        if config is None:
            return api._send_raw_message(
                chat_id,
                "Служба заботы сейчас недоступна. Попробуй позже.",
            )

        thread_id = _ensure_topic(
            api,
            config.chat_id,
            chat_id,
            config.message_thread_id,
        )
        try:
            care_message_id = care._send_group_message(
                api,
                config.chat_id,
                (
                    f"↩️ Ответ пользователя по обращению №{feedback_id}\n\n"
                    f"{body}"
                ),
                message_thread_id=thread_id,
            )
        except base.TelegramDeliveryError:
            return api._send_raw_message(
                chat_id,
                "Не смог передать ответ в службу заботы. Попробуй позже.",
            )

        if care_message_id is not None:
            care.CARE.link(
                feedback_id,
                care_message_id=care_message_id,
                direction="user_to_care",
            )
        return api._send_raw_message(
            chat_id,
            f"🛟 Сообщение добавлено в обращение №{feedback_id}.",
            _user_keyboard(chat_id),
        )

    return _original_send_guarded_message(
        api,
        chat_id,
        text,
        recent_messages=recent_messages,
        mode=mode,
        keyboard=keyboard,
    )


def deliver_pending_feedback(api: base.TelegramAPI, store) -> None:
    config = care.CARE.get_config()
    if config is None:
        return _original_deliver_pending_feedback(api, store)

    for feedback in store.pending_feedback():
        category_label = (
            "🛟 Нужна помощь команды"
            if feedback.category == "help"
            else "📝 Наблюдение"
        )
        thread_id = _ensure_topic(
            api,
            config.chat_id,
            feedback.chat_id,
            config.message_thread_id,
        )
        notice = (
            f"{category_label} · обращение №{feedback.id}\n"
            f"Время: {feedback.created_at.isoformat()}\n"
            f"Режим: {feedback.mode_at_submit}\n\n"
            f"{feedback.body}\n\n"
            "↩️ Ответь Reply на это сообщение — бот доставит ответ пользователю."
        )
        status, message_id = base.deliver_telegram(
            lambda: care._send_group_message(
                api,
                config.chat_id,
                notice,
                message_thread_id=thread_id,
            )
        )
        store.mark_feedback_notification(feedback.id, status, message_id)
        if status == "confirmed" and message_id is not None:
            care.CARE.link(
                feedback.id,
                care_message_id=message_id,
                direction="user_to_care",
            )
            base.deliver_telegram(
                lambda: api._send_raw_message(
                    feedback.chat_id,
                    (
                        "🛟 Принял. Сообщение передано службе заботы.\n"
                        "Ответ придёт сюда, в этот чат."
                    ),
                    _user_keyboard(feedback.chat_id),
                )
            )


base.extract_text_message = extract_text_message
base.TelegramAPI.send_guarded_message = send_guarded_message
care.send_guarded_message = send_guarded_message
base.deliver_pending_feedback = deliver_pending_feedback
care.deliver_pending_feedback = deliver_pending_feedback


def run() -> None:
    runtime.run()


if __name__ == "__main__":
    run()
