from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx


class TelegramWebhookSetupError(RuntimeError):
    pass


@dataclass(frozen=True)
class TelegramWebhookInfo:
    url: str
    pending_update_count: int
    last_error_message: str | None


def _required_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise TelegramWebhookSetupError(f"Missing required environment variable: {name}")
    return value


def resolve_webhook_url() -> str:
    explicit = (os.getenv("NENOY_V2_PUBLIC_BASE_URL") or "").strip()
    if explicit:
        base = explicit.rstrip("/")
    else:
        domain = (os.getenv("RAILWAY_PUBLIC_DOMAIN") or "").strip()
        if not domain:
            raise TelegramWebhookSetupError(
                "Missing NENOY_V2_PUBLIC_BASE_URL or RAILWAY_PUBLIC_DOMAIN"
            )
        base = domain if domain.startswith(("http://", "https://")) else f"https://{domain}"
        base = base.rstrip("/")
    return f"{base}/webhooks/telegram"


def configure_webhook(
    *,
    token: str,
    secret: str,
    webhook_url: str,
    client: Any | None = None,
) -> TelegramWebhookInfo:
    owns_client = client is None
    http_client = client or httpx.Client(timeout=15.0)
    api_base = f"https://api.telegram.org/bot{token}"
    try:
        try:
            response = http_client.post(
                f"{api_base}/setWebhook",
                json={
                    "url": webhook_url,
                    "secret_token": secret,
                    "allowed_updates": ["message", "edited_message", "message_reaction"],
                    "drop_pending_updates": False,
                },
            )
            payload = response.json()
            if response.status_code >= 400 or not payload.get("ok"):
                raise TelegramWebhookSetupError("Telegram setWebhook request was rejected")

            info_response = http_client.get(f"{api_base}/getWebhookInfo")
            info_payload = info_response.json()
            if info_response.status_code >= 400 or not info_payload.get("ok"):
                raise TelegramWebhookSetupError("Telegram getWebhookInfo request was rejected")
        except TelegramWebhookSetupError:
            raise
        except Exception as exc:
            raise TelegramWebhookSetupError("Telegram webhook API request failed") from exc

        result = dict(info_payload.get("result") or {})
        configured_url = str(result.get("url") or "")
        if configured_url != webhook_url:
            raise TelegramWebhookSetupError("Telegram webhook URL verification failed")

        return TelegramWebhookInfo(
            url=configured_url,
            pending_update_count=int(result.get("pending_update_count") or 0),
            last_error_message=(str(result["last_error_message"]) if result.get("last_error_message") else None),
        )
    finally:
        if owns_client:
            http_client.close()


def main() -> None:
    token = _required_env("NENOY_V2_TELEGRAM_BOT_TOKEN")
    secret = _required_env("NENOY_V2_WEBHOOK_SECRET")
    webhook_url = resolve_webhook_url()
    info = configure_webhook(token=token, secret=secret, webhook_url=webhook_url)

    # Never print token or secret. This output is safe for Railway deploy logs.
    print(
        "Telegram webhook configured:",
        f"url={info.url}",
        f"pending_update_count={info.pending_update_count}",
        f"last_error_message={info.last_error_message or '-'}",
    )


if __name__ == "__main__":
    main()
