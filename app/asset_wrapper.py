from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from . import telegram_bot as base
from .config import AppConfig
from .support import load_catalog


ASSET_REGISTRATION_ENABLED = os.getenv(
    "NENOYBOT_ASSET_REGISTRATION_ENABLED", ""
).strip().lower() in {"1", "true", "yes", "on"}

CATALOG = load_catalog()
CONTENT_BY_ID = {item["id"]: item for item in CATALOG}
CONTENT_ID_BY_TEXT = {item["text"]: item["id"] for item in CATALOG}


@dataclass(frozen=True)
class PendingAsset:
    chat_id: int
    from_id: int | None
    file_id: str


_PENDING_ASSETS: dict[int, PendingAsset] = {}


class AssetStore:
    def __init__(self, database_url: str | None) -> None:
        self.database_url = database_url
        self._memory: dict[str, str] = {}
        self.ensure_schema()

    def _connect(self):
        if not self.database_url:
            return None
        import psycopg
        return psycopg.connect(self.database_url, autocommit=True)

    def ensure_schema(self) -> None:
        if not self.database_url:
            return
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS nenoy_lightness_assets (
                        content_id TEXT PRIMARY KEY,
                        telegram_file_id TEXT NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )

    def set(self, content_id: str, file_id: str) -> None:
        if not self.database_url:
            self._memory[content_id] = file_id
            return
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO nenoy_lightness_assets
                        (content_id, telegram_file_id, updated_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (content_id)
                    DO UPDATE SET
                        telegram_file_id = EXCLUDED.telegram_file_id,
                        updated_at = NOW()
                    """,
                    (content_id, file_id),
                )

    def get(self, content_id: str) -> str | None:
        if not self.database_url:
            return self._memory.get(content_id)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT telegram_file_id
                    FROM nenoy_lightness_assets
                    WHERE content_id = %s
                    """,
                    (content_id,),
                )
                row = cursor.fetchone()
        return row[0] if row else None


ASSETS = AssetStore(AppConfig.database_url)

_original_extract_text_message = base.extract_text_message
_original_build_reply = base.build_reply
_original_send_guarded_message = base.TelegramAPI.send_guarded_message


def _image_file_id(message: dict[str, Any]) -> str | None:
    photos = message.get("photo")
    if isinstance(photos, list) and photos:
        candidates = [item for item in photos if isinstance(item, dict) and item.get("file_id")]
        if candidates:
            best = max(
                candidates,
                key=lambda item: (
                    int(item.get("width") or 0) * int(item.get("height") or 0),
                    int(item.get("file_size") or 0),
                ),
            )
            return str(best["file_id"])

    document = message.get("document")
    if isinstance(document, dict):
        mime_type = str(document.get("mime_type") or "")
        file_id = document.get("file_id")
        if mime_type.startswith("image/") and file_id:
            return str(file_id)
    return None


def extract_text_message(update: dict[str, Any]) -> tuple[int, str] | None:
    extracted = _original_extract_text_message(update)
    if extracted is not None:
        return extracted

    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return None

    caption = message.get("caption")
    chat = message.get("chat")
    if not isinstance(caption, str) or not isinstance(chat, dict):
        return None

    chat_id = chat.get("id")
    if not isinstance(chat_id, int):
        return None

    file_id = _image_file_id(message)
    if file_id:
        sender = message.get("from") or {}
        from_id = sender.get("id") if isinstance(sender, dict) else None
        _PENDING_ASSETS[chat_id] = PendingAsset(chat_id, from_id, file_id)

    return chat_id, caption.strip()


def build_reply(
    chat_id: int,
    text: str,
    store,
    runtime_state=None,
) -> str:
    if not text.startswith("/asset"):
        return _original_build_reply(chat_id, text, store, runtime_state)

    pending = _PENDING_ASSETS.pop(chat_id, None)

    if not ASSET_REGISTRATION_ENABLED:
        return "Регистрация изображений выключена."

    if pending is None:
        return "Пришли изображение с подписью `/asset M01`."

    if pending.from_id not in AppConfig.care_admin_ids:
        return "Команда недоступна."

    parts = text.split(maxsplit=1)
    if len(parts) != 2:
        return "Формат: `/asset M01`."

    content_id = parts[1].strip().upper()
    if content_id not in CONTENT_BY_ID:
        return f"Неизвестный content_id: {content_id}."

    ASSETS.set(content_id, pending.file_id)
    return f"🖼 {content_id} зарегистрирован. Следующая отправка этого сообщения будет с картинкой."


def _send_photo(
    api: base.TelegramAPI,
    chat_id: int,
    file_id: str,
    caption: str,
    keyboard=None,
) -> int | None:
    result = api.request(
        "sendPhoto",
        {
            "chat_id": str(chat_id),
            "photo": file_id,
            "caption": caption,
            "reply_markup": base.json.dumps(
                keyboard or base.SUPPORT_KEYBOARD,
                ensure_ascii=False,
            ),
        },
    )
    return result.get("message_id") if isinstance(result, dict) else None


def send_guarded_message(
    api: base.TelegramAPI,
    chat_id: int,
    text: str,
    recent_messages=(),
    mode: str = "coach",
    keyboard=None,
) -> int | None:
    if mode == "support":
        content_id = CONTENT_ID_BY_TEXT.get(text)
        if content_id:
            file_id = ASSETS.get(content_id)
            if file_id:
                return _send_photo(
                    api,
                    chat_id,
                    file_id,
                    text,
                    keyboard or base.SUPPORT_KEYBOARD,
                )

    return _original_send_guarded_message(
        api,
        chat_id,
        text,
        recent_messages=recent_messages,
        mode=mode,
        keyboard=keyboard,
    )


base.extract_text_message = extract_text_message
base.build_reply = build_reply
base.TelegramAPI.send_guarded_message = send_guarded_message


def run() -> None:
    base.run_telegram_bot()


if __name__ == "__main__":
    run()
