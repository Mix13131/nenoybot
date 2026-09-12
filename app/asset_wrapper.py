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

BUTTON_MODE = "🎛 Режим"
BUTTON_LIGHTNESS = "🌿 Лёгкость"
BUTTON_BACK = "↩️ Назад"
ASSET_IMAGE_RECEIVED = "__asset_image_received__"
ASSET_SHOW_PREFIX = "__asset_show__:"

MODE_MENU_TEXT = (
    "Как мне быть рядом сегодня?\n\n"
    "🔥 Тренер\n"
    "Когда нужен результат. Держу фокус, режу отговорки и возвращаю к следующему конкретному действию.\n\n"
    "🌿 Лёгкость\n"
    "Когда хочется убрать внутренний экзамен. Возвращаю интерес, игру, любопытство и помогаю двигаться без лишнего напряжения.\n\n"
    "Переключаться можно в любой момент."
)

MODE_KEYBOARD = {
    "keyboard": [
        [{"text": base.BUTTON_COACH}, {"text": BUTTON_LIGHTNESS}],
        [{"text": BUTTON_BACK}],
    ],
    "resize_keyboard": True,
    "one_time_keyboard": False,
    "is_persistent": True,
}

base.MAIN_KEYBOARD = {
    "keyboard": [
        [{"text": base.BUTTON_SET_GOAL}, {"text": base.BUTTON_REPORT}],
        [{"text": base.BUTTON_KICK}, {"text": base.BUTTON_HELP}],
        [{"text": base.BUTTON_CLEAR_GOAL}],
        [{"text": BUTTON_MODE}, {"text": base.BUTTON_FEEDBACK}],
    ],
    "resize_keyboard": True,
    "one_time_keyboard": False,
    "is_persistent": True,
}

base.SUPPORT_KEYBOARD = {
    "keyboard": [
        [{"text": base.BUTTON_SUPPORT_NOW}, {"text": base.BUTTON_SCHEDULE}],
        [{"text": base.BUTTON_FEEDBACK}],
        [{"text": base.BUTTON_PAUSE}, {"text": BUTTON_MODE}],
    ],
    "resize_keyboard": True,
    "one_time_keyboard": False,
    "is_persistent": True,
}

base.BOT_COMMANDS = (
    {"command": "start", "description": "Запустить НеНойBot"},
    {"command": "goal", "description": "Задать цель: /goal результат + срок"},
    {"command": "clear_goal", "description": "Сбросить цель"},
    {"command": "help", "description": "Показать команды"},
    {"command": "modes", "description": "Выбрать режим"},
    {"command": "support", "description": "Режим «Лёгкость»"},
    {"command": "coach", "description": "Режим «Тренер»"},
    {"command": "feedback", "description": "Наблюдения по НеНойBot"},
)


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
            print("НеНойBot asset store: in-memory mode.")
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
        print("НеНойBot asset store schema ready.")

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

    def list_ids(self) -> list[str]:
        if not self.database_url:
            return sorted(self._memory)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT content_id
                    FROM nenoy_lightness_assets
                    ORDER BY content_id
                    """
                )
                rows = cursor.fetchall()
        return [str(row[0]) for row in rows]


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
    message = update.get("message") or update.get("edited_message")
    if isinstance(message, dict):
        chat = message.get("chat")
        chat_id = chat.get("id") if isinstance(chat, dict) else None
        chat_type = chat.get("type") if isinstance(chat, dict) else None
        file_id = _image_file_id(message)

        if isinstance(chat_id, int) and chat_type == "private" and file_id:
            sender = message.get("from") or {}
            from_id = sender.get("id") if isinstance(sender, dict) else None
            _PENDING_ASSETS[chat_id] = PendingAsset(chat_id, from_id, file_id)
            caption = message.get("caption")
            if isinstance(caption, str) and caption.strip():
                return chat_id, caption.strip()
            return chat_id, ASSET_IMAGE_RECEIVED

    return _original_extract_text_message(update)


def build_reply(
    chat_id: int,
    text: str,
    store,
    runtime_state=None,
) -> str:
    if text == ASSET_IMAGE_RECEIVED:
        pending = _PENDING_ASSETS.get(chat_id)
        if not ASSET_REGISTRATION_ENABLED:
            _PENDING_ASSETS.pop(chat_id, None)
            return "Регистрация изображений выключена."
        if pending is None:
            return "Фото не удалось прочитать. Пришли его ещё раз."
        if pending.from_id not in AppConfig.care_admin_ids:
            _PENDING_ASSETS.pop(chat_id, None)
            return "Команда недоступна."
        return "Фото поймал 🖼 Теперь пришли отдельным сообщением `/asset M01`."

    if text.startswith("/asset_show"):
        if chat_id not in AppConfig.care_admin_ids:
            return "Команда недоступна."
        parts = text.split(maxsplit=1)
        if len(parts) != 2:
            return "Формат: `/asset_show D01`."
        content_id = parts[1].strip().upper()
        if content_id not in CONTENT_BY_ID:
            return f"Неизвестный content_id: {content_id}."
        if not ASSETS.get(content_id):
            return f"🖼 {content_id} пока не зарегистрирован."
        return f"{ASSET_SHOW_PREFIX}{content_id}"

    if text.strip() == "/asset_list":
        if chat_id not in AppConfig.care_admin_ids:
            return "Команда недоступна."
        registered = ASSETS.list_ids()
        registered_set = set(registered)
        missing = [content_id for content_id in sorted(CONTENT_BY_ID) if content_id not in registered_set]
        by_prefix = {
            prefix: [content_id for content_id in registered if content_id.startswith(prefix)]
            for prefix in ("M", "D", "A")
        }
        return "\n".join([
            f"🖼 Зарегистрировано: {len(registered)}/{len(CONTENT_BY_ID)}",
            f"M: {' '.join(by_prefix['M']) or '—'}",
            f"D: {' '.join(by_prefix['D']) or '—'}",
            f"A: {' '.join(by_prefix['A']) or '—'}",
            f"Не хватает: {' '.join(missing) if missing else 'ничего — комплект полный ✅'}",
        ])

    if text.startswith("/asset"):
        pending = _PENDING_ASSETS.pop(chat_id, None)

        if not ASSET_REGISTRATION_ENABLED:
            return "Регистрация изображений выключена."

        if pending is None:
            return "Сначала пришли изображение, затем `/asset M01`, или добавь `/asset M01` подписью к фото."

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

    if text.startswith("/start") or text.startswith("/modes") or text == BUTTON_MODE:
        if runtime_state is not None:
            runtime_state.clear(chat_id)
        return MODE_MENU_TEXT

    if text == BUTTON_LIGHTNESS or (
        text.startswith("/support")
        and not text.startswith(("/support_now", "/support_schedule", "/support_pause", "/support_resume", "/support_off"))
    ):
        if runtime_state is not None:
            runtime_state.clear(chat_id)
        store.set_mode(chat_id, "support")
        return (
            "🌿 Режим «Лёгкость» включён. Здесь не нужно ничего доказывать — "
            "можно разбираться, пробовать и двигаться через интерес."
        )

    if text.startswith("/coach") or text == base.BUTTON_COACH:
        if runtime_state is not None:
            runtime_state.clear(chat_id)
        store.set_mode(chat_id, "coach")
        return (
            "🔥 Режим «Тренер» включён. Держу фокус, режу отговорки "
            "и возвращаю к следующему конкретному действию."
        )

    if text == BUTTON_BACK:
        return _original_build_reply(chat_id, "/help", store, runtime_state)

    if text.startswith("/help") or text == base.BUTTON_HELP:
        return (
            _original_build_reply(chat_id, text, store, runtime_state)
            + "\n\n🎛 Режим — выбрать «🔥 Тренер» или «🌿 Лёгкость»."
        )

    return _original_build_reply(chat_id, text, store, runtime_state)


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
    if text.startswith(ASSET_SHOW_PREFIX):
        content_id = text.removeprefix(ASSET_SHOW_PREFIX).strip().upper()
        item = CONTENT_BY_ID.get(content_id)
        file_id = ASSETS.get(content_id)
        if not item or not file_id:
            return api._send_raw_message(chat_id, f"🖼 {content_id} не найден.")
        caption = f"🖼 {content_id}\n\n{item['text']}"
        if len(caption) > 1024:
            caption = caption[:1021].rstrip() + "…"
        return _send_photo(
            api,
            chat_id,
            file_id,
            caption,
            keyboard or (base.SUPPORT_KEYBOARD if mode == "support" else base.MAIN_KEYBOARD),
        )

    if text == MODE_MENU_TEXT:
        return api._send_raw_message(chat_id, text, MODE_KEYBOARD)

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
    print("НеНойBot asset wrapper active.")
    base.run_telegram_bot()


if __name__ == "__main__":
    run()
