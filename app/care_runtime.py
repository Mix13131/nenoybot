from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from . import schedule_runtime as runtime
from . import asset_wrapper as app
from . import telegram_bot as base
from . import memory_store as memory_store_module
from .config import AppConfig
from .support import parse_schedule


CARE_SETUP_SENTINEL = "__care_setup__"
CARE_GROUP_REPLY_SENTINEL = "__care_group_reply__"
CARE_USER_REPLY_SENTINEL = "__care_user_reply__"
CARE_SETUP_OK_PREFIX = "__care_setup_ok__:"

_PENDING_SETUP: dict[int, tuple[int | None, str | None, int | None]] = {}
_PENDING_GROUP_REPLY: dict[int, tuple[int, str]] = {}
_PENDING_USER_REPLY: dict[int, tuple[int, str]] = {}


@dataclass(frozen=True)
class CareConfig:
    chat_id: int
    message_thread_id: int | None = None


class CareStore:
    def __init__(self, database_url: str | None) -> None:
        self.database_url = database_url
        self._config: CareConfig | None = None
        self._care_links: dict[int, int] = {}
        self._user_links: dict[int, int] = {}
        self._activation: dict[int, datetime] = {}
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
                CREATE TABLE IF NOT EXISTS nenoy_care_config (
                    id SMALLINT PRIMARY KEY CHECK (id = 1),
                    chat_id BIGINT NOT NULL,
                    message_thread_id BIGINT,
                    title TEXT,
                    configured_by BIGINT,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS nenoy_care_links (
                    id BIGSERIAL PRIMARY KEY,
                    feedback_id BIGINT NOT NULL,
                    care_message_id BIGINT,
                    user_message_id BIGINT,
                    direction TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS nenoy_care_links_care_message_idx
                ON nenoy_care_links(care_message_id)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS nenoy_care_links_user_message_idx
                ON nenoy_care_links(user_message_id)
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS nenoy_support_activation (
                    chat_id BIGINT PRIMARY KEY,
                    activated_at TIMESTAMPTZ NOT NULL
                )
                """
            )

    def get_config(self) -> CareConfig | None:
        if not self.database_url:
            return self._config
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT chat_id, message_thread_id FROM nenoy_care_config WHERE id = 1"
            )
            row = cursor.fetchone()
        if not row:
            return None
        return CareConfig(int(row[0]), int(row[1]) if row[1] is not None else None)

    def set_config(
        self,
        chat_id: int,
        message_thread_id: int | None,
        title: str | None,
        configured_by: int | None,
    ) -> None:
        config = CareConfig(chat_id, message_thread_id)
        if not self.database_url:
            self._config = config
            return
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO nenoy_care_config
                    (id, chat_id, message_thread_id, title, configured_by, updated_at)
                VALUES (1, %s, %s, %s, %s, NOW())
                ON CONFLICT (id) DO UPDATE SET
                    chat_id = EXCLUDED.chat_id,
                    message_thread_id = EXCLUDED.message_thread_id,
                    title = EXCLUDED.title,
                    configured_by = EXCLUDED.configured_by,
                    updated_at = NOW()
                """,
                (chat_id, message_thread_id, title, configured_by),
            )

    def feedback_for_care_message(self, message_id: int) -> int | None:
        if not self.database_url:
            return self._care_links.get(message_id)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id
                FROM nenoy_feedback
                WHERE notification_message_id = %s
                UNION ALL
                SELECT feedback_id
                FROM nenoy_care_links
                WHERE care_message_id = %s
                ORDER BY 1 DESC
                LIMIT 1
                """,
                (message_id, message_id),
            )
            row = cursor.fetchone()
        return int(row[0]) if row else None

    def feedback_for_user_message(self, message_id: int) -> int | None:
        if not self.database_url:
            return self._user_links.get(message_id)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT feedback_id
                FROM nenoy_care_links
                WHERE user_message_id = %s
                ORDER BY id DESC
                LIMIT 1
                """,
                (message_id,),
            )
            row = cursor.fetchone()
        return int(row[0]) if row else None

    def get_user_chat_id(self, feedback_id: int) -> int | None:
        if not self.database_url:
            return None
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT chat_id FROM nenoy_feedback WHERE id = %s", (feedback_id,))
            row = cursor.fetchone()
        return int(row[0]) if row else None

    def latest_care_message_id(self, feedback_id: int) -> int | None:
        if not self.database_url:
            candidates = [
                message_id
                for message_id, linked_feedback_id in self._care_links.items()
                if linked_feedback_id == feedback_id
            ]
            return max(candidates) if candidates else None
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT care_message_id
                FROM nenoy_care_links
                WHERE feedback_id = %s AND care_message_id IS NOT NULL
                ORDER BY id DESC
                LIMIT 1
                """,
                (feedback_id,),
            )
            row = cursor.fetchone()
            if row:
                return int(row[0])
            cursor.execute(
                "SELECT notification_message_id FROM nenoy_feedback WHERE id = %s",
                (feedback_id,),
            )
            row = cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def link(
        self,
        feedback_id: int,
        *,
        care_message_id: int | None = None,
        user_message_id: int | None = None,
        direction: str,
    ) -> None:
        if not self.database_url:
            if care_message_id is not None:
                self._care_links[care_message_id] = feedback_id
            if user_message_id is not None:
                self._user_links[user_message_id] = feedback_id
            return
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO nenoy_care_links
                    (feedback_id, care_message_id, user_message_id, direction)
                VALUES (%s, %s, %s, %s)
                """,
                (feedback_id, care_message_id, user_message_id, direction),
            )

    def mark_replied(self, feedback_id: int) -> None:
        if not self.database_url:
            return
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE nenoy_feedback
                SET status = 'replied', replied_at = NOW()
                WHERE id = %s
                """,
                (feedback_id,),
            )

    def set_activation(self, chat_id: int, activated_at: datetime) -> None:
        activated_at = activated_at.astimezone(UTC)
        if not self.database_url:
            self._activation[chat_id] = activated_at
            return
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO nenoy_support_activation(chat_id, activated_at)
                VALUES (%s, %s)
                ON CONFLICT (chat_id) DO UPDATE SET activated_at = EXCLUDED.activated_at
                """,
                (chat_id, activated_at),
            )

    def clear_activation(self, chat_id: int) -> None:
        if not self.database_url:
            self._activation.pop(chat_id, None)
            return
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM nenoy_support_activation WHERE chat_id = %s", (chat_id,))

    def get_activation(self, chat_id: int) -> datetime | None:
        if not self.database_url:
            return self._activation.get(chat_id)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT activated_at FROM nenoy_support_activation WHERE chat_id = %s",
                (chat_id,),
            )
            row = cursor.fetchone()
        return row[0] if row else None


CARE = CareStore(AppConfig.database_url)

_stored_config = CARE.get_config()
if _stored_config is not None:
    AppConfig.care_chat_id = _stored_config.chat_id

_original_extract_text_message = base.extract_text_message
_original_build_reply = base.build_reply
_original_send_guarded_message = base.TelegramAPI.send_guarded_message
_original_deliver_pending_feedback = base.deliver_pending_feedback
_original_due_slots = memory_store_module.due_slots


def due_slots(settings, now):
    slots = _original_due_slots(settings, now)
    activated_at = CARE.get_activation(settings.chat_id)
    if activated_at is None:
        return slots
    return [slot for slot in slots if slot[2] >= activated_at]


memory_store_module.due_slots = due_slots


def _send_group_message(
    api: base.TelegramAPI,
    chat_id: int,
    text: str,
    *,
    message_thread_id: int | None = None,
    reply_to_message_id: int | None = None,
) -> int | None:
    payload: dict[str, str] = {
        "chat_id": str(chat_id),
        "text": text,
    }
    if message_thread_id is not None:
        payload["message_thread_id"] = str(message_thread_id)
    if reply_to_message_id is not None:
        payload["reply_parameters"] = base.json.dumps(
            {"message_id": reply_to_message_id},
            ensure_ascii=False,
        )
    result = api.request("sendMessage", payload)
    return result.get("message_id") if isinstance(result, dict) else None


def extract_text_message(update: dict[str, Any]) -> tuple[int, str] | None:
    message = update.get("message") or update.get("edited_message")
    if isinstance(message, dict):
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        text = message.get("text")
        chat_id = chat.get("id")
        chat_type = chat.get("type")
        from_id = sender.get("id")
        message_thread_id = message.get("message_thread_id")

        if (
            isinstance(chat_id, int)
            and chat_type in {"group", "supergroup"}
            and isinstance(text, str)
            and text.strip().startswith("/care_setup")
        ):
            title = chat.get("title")
            _PENDING_SETUP[chat_id] = (
                int(from_id) if isinstance(from_id, int) else None,
                str(title) if title else None,
                int(message_thread_id) if isinstance(message_thread_id, int) else None,
            )
            return chat_id, CARE_SETUP_SENTINEL

        config = CARE.get_config()
        if (
            config is not None
            and isinstance(chat_id, int)
            and chat_id == config.chat_id
            and chat_type in {"group", "supergroup"}
            and isinstance(text, str)
        ):
            reply_to = message.get("reply_to_message")
            reply_id = reply_to.get("message_id") if isinstance(reply_to, dict) else None
            if isinstance(reply_id, int):
                feedback_id = CARE.feedback_for_care_message(reply_id)
                if feedback_id is not None:
                    _PENDING_GROUP_REPLY[chat_id] = (feedback_id, text.strip())
                    return chat_id, CARE_GROUP_REPLY_SENTINEL

        if (
            isinstance(chat_id, int)
            and chat_type == "private"
            and isinstance(text, str)
        ):
            reply_to = message.get("reply_to_message")
            reply_id = reply_to.get("message_id") if isinstance(reply_to, dict) else None
            if isinstance(reply_id, int):
                feedback_id = CARE.feedback_for_user_message(reply_id)
                if feedback_id is not None:
                    _PENDING_USER_REPLY[chat_id] = (feedback_id, text.strip())
                    return chat_id, CARE_USER_REPLY_SENTINEL

    return _original_extract_text_message(update)


def _will_enable_schedule(chat_id: int, text: str, store) -> bool:
    stripped = text.strip()
    if stripped == runtime.BUTTON_ENABLE_DEFAULT:
        return True

    if stripped.startswith("/support_schedule "):
        value = stripped.removeprefix("/support_schedule").strip()
        try:
            parse_schedule(value)
        except ValueError:
            return False
        return True

    if chat_id in runtime._PENDING_EDIT and not stripped.startswith("/"):
        settings = store.get_support_settings(chat_id)
        timezone = runtime._timezone_for(settings)
        compact_times = "".join(stripped.split())
        try:
            parse_schedule(f"{compact_times} {timezone}")
        except ValueError:
            return False
        return True

    return False


def build_reply(chat_id: int, text: str, store, runtime_state=None) -> str:
    if text == CARE_SETUP_SENTINEL:
        pending = _PENDING_SETUP.pop(chat_id, None)
        if pending is None:
            return "Настройка группы не найдена."
        from_id, title, message_thread_id = pending
        if from_id not in AppConfig.care_admin_ids:
            return "Команда недоступна."
        CARE.set_config(chat_id, message_thread_id, title, from_id)
        AppConfig.care_chat_id = chat_id
        return CARE_SETUP_OK_PREFIX + (
            "✅ Служба заботы подключена к этой группе.\n\n"
            "Новые «🛟 Нужна помощь» и «📝 Наблюдение» будут приходить сюда. "
            "Чтобы ответить пользователю, просто сделай Reply на сообщение обращения."
        )

    if text == CARE_GROUP_REPLY_SENTINEL:
        return CARE_GROUP_REPLY_SENTINEL

    if text == CARE_USER_REPLY_SENTINEL:
        return CARE_USER_REPLY_SENTINEL

    if _will_enable_schedule(chat_id, text, store):
        CARE.set_activation(chat_id, datetime.now(UTC))
    elif text.strip() == runtime.BUTTON_DISABLE or text.strip().startswith("/support_off"):
        CARE.clear_activation(chat_id)

    return _original_build_reply(chat_id, text, store, runtime_state)


def send_guarded_message(
    api: base.TelegramAPI,
    chat_id: int,
    text: str,
    recent_messages=(),
    mode: str = "coach",
    keyboard=None,
) -> int | None:
    if text.startswith(CARE_SETUP_OK_PREFIX):
        return api._send_raw_message(
            chat_id,
            text.removeprefix(CARE_SETUP_OK_PREFIX),
        )

    if text == CARE_GROUP_REPLY_SENTINEL:
        pending = _PENDING_GROUP_REPLY.pop(chat_id, None)
        if pending is None:
            return api._send_raw_message(chat_id, "Не смог связать ответ с обращением.")
        feedback_id, body = pending
        user_chat_id = CARE.get_user_chat_id(feedback_id)
        if user_chat_id is None:
            return api._send_raw_message(chat_id, "Пользователь обращения не найден.")

        status, user_message_id = base.deliver_telegram(
            lambda: api._send_raw_message(
                user_chat_id,
                (
                    f"🛟 Ответ службы заботы по обращению №{feedback_id}\n\n"
                    f"{body}\n\n"
                    "Можно ответить прямо на это сообщение — ответ вернётся в службу заботы."
                ),
            )
        )
        if status == "confirmed" and user_message_id is not None:
            CARE.link(
                feedback_id,
                user_message_id=user_message_id,
                direction="care_to_user",
            )
            CARE.mark_replied(feedback_id)
            return api._send_raw_message(
                chat_id,
                f"✅ Ответ доставлен пользователю по обращению №{feedback_id}.",
            )
        return api._send_raw_message(
            chat_id,
            f"⚠️ Ответ по обращению №{feedback_id} не доставлен: {status}.",
        )

    if text == CARE_USER_REPLY_SENTINEL:
        pending = _PENDING_USER_REPLY.pop(chat_id, None)
        if pending is None:
            return api._send_raw_message(chat_id, "Не смог связать ответ с обращением.")
        feedback_id, body = pending
        config = CARE.get_config()
        if config is None:
            return api._send_raw_message(
                chat_id,
                "Служба заботы сейчас недоступна. Попробуй позже.",
            )
        target_message_id = CARE.latest_care_message_id(feedback_id)
        try:
            care_message_id = _send_group_message(
                api,
                config.chat_id,
                (
                    f"↩️ Ответ пользователя по обращению №{feedback_id}\n\n"
                    f"{body}"
                ),
                message_thread_id=config.message_thread_id,
                reply_to_message_id=target_message_id,
            )
        except base.TelegramDeliveryError:
            return api._send_raw_message(
                chat_id,
                "Не смог передать ответ в службу заботы. Попробуй позже.",
            )

        if care_message_id is not None:
            CARE.link(
                feedback_id,
                care_message_id=care_message_id,
                direction="user_to_care",
            )
        return api._send_raw_message(
            chat_id,
            f"🛟 Сообщение добавлено в обращение №{feedback_id}.",
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
    config = CARE.get_config()
    if config is None:
        return _original_deliver_pending_feedback(api, store)

    for feedback in store.pending_feedback():
        category_label = (
            "🛟 Нужна помощь"
            if feedback.category == "help"
            else "📝 Наблюдение"
        )
        notice = (
            f"{category_label} · обращение №{feedback.id}\n"
            f"Время: {feedback.created_at.isoformat()}\n"
            f"Режим: {feedback.mode_at_submit}\n\n"
            f"{feedback.body}\n\n"
            "↩️ Ответь Reply на это сообщение — бот доставит ответ пользователю."
        )
        status, message_id = base.deliver_telegram(
            lambda: _send_group_message(
                api,
                config.chat_id,
                notice,
                message_thread_id=config.message_thread_id,
            )
        )
        store.mark_feedback_notification(feedback.id, status, message_id)
        if status == "confirmed" and message_id is not None:
            CARE.link(
                feedback.id,
                care_message_id=message_id,
                direction="user_to_care",
            )
            base.deliver_telegram(
                lambda: api._send_raw_message(
                    feedback.chat_id,
                    (
                        f"Обращение №{feedback.id} передано в службу заботы. "
                        "Ответ появится здесь."
                    ),
                )
            )


base.extract_text_message = extract_text_message
base.build_reply = build_reply
app.build_reply = build_reply
base.TelegramAPI.send_guarded_message = send_guarded_message
app.send_guarded_message = send_guarded_message
base.deliver_pending_feedback = deliver_pending_feedback


def run() -> None:
    runtime.run()


if __name__ == "__main__":
    run()
