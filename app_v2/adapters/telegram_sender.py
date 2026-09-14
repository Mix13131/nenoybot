from __future__ import annotations

import os
from typing import Any

import httpx


class TelegramSenderConfigurationError(RuntimeError):
    pass


class TelegramSendError(RuntimeError):
    pass


class TelegramSender:
    def __init__(
        self,
        token: str | None = None,
        *,
        client: httpx.Client | Any | None = None,
        base_url: str = "https://api.telegram.org",
        timeout_seconds: float = 10.0,
    ) -> None:
        resolved = (token or os.getenv("NENOY_V2_TELEGRAM_BOT_TOKEN") or "").strip()
        if not resolved:
            raise TelegramSenderConfigurationError(
                "Не задан NENOY_V2_TELEGRAM_BOT_TOKEN для Telegram sender НеНой 2.0"
            )
        self.token = resolved
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=timeout_seconds)

    def send(self, destination_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise TelegramSendError("Outbound Telegram payload must contain non-empty text")

        request_payload: dict[str, Any] = {
            "chat_id": destination_id,
            "text": text,
        }
        reply_to = payload.get("reply_to_message_id")
        if reply_to is not None:
            try:
                message_id: int | str = int(reply_to)
            except (TypeError, ValueError):
                message_id = str(reply_to)
            request_payload["reply_parameters"] = {"message_id": message_id}

        response = self.client.post(
            f"{self.base_url}/bot{self.token}/sendMessage",
            json=request_payload,
        )
        if getattr(response, "status_code", 500) >= 400:
            body = getattr(response, "text", "")
            raise TelegramSendError(
                f"Telegram HTTP {response.status_code}: {body[:1000]}"
            )

        try:
            data = response.json()
        except Exception as exc:
            raise TelegramSendError("Telegram returned invalid JSON") from exc

        if not data.get("ok"):
            raise TelegramSendError(
                f"Telegram API error: {data.get('description') or 'unknown error'}"
            )
        result = data.get("result")
        return result if isinstance(result, dict) else {"result": result}
