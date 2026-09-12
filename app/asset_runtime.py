from __future__ import annotations

from . import asset_wrapper as app
from . import telegram_bot as base


_original_send_photo = app._send_photo


def _send_asset(
    api: base.TelegramAPI,
    chat_id: int,
    file_id: str,
    caption: str,
    keyboard=None,
) -> int | None:
    try:
        return _original_send_photo(api, chat_id, file_id, caption, keyboard)
    except base.TelegramDeliveryError as exc:
        if "Document as Photo" not in str(exc):
            raise

    result = api.request(
        "sendDocument",
        {
            "chat_id": str(chat_id),
            "document": file_id,
            "caption": caption,
            "reply_markup": base.json.dumps(
                keyboard or base.SUPPORT_KEYBOARD,
                ensure_ascii=False,
            ),
        },
    )
    return result.get("message_id") if isinstance(result, dict) else None


app._send_photo = _send_asset


def run() -> None:
    app.run()


if __name__ == "__main__":
    run()
