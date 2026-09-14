from __future__ import annotations

import secrets
from typing import Any

from fastapi import FastAPI, Header, HTTPException

from .config import AppConfig, load_config
from .services.event_ingestor import ingest_telegram_update


config: AppConfig = load_config()
app = FastAPI(title=config.app_name)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": config.service_name,
    }


@app.get("/ready")
def ready() -> dict[str, str]:
    return {
        "status": "ready",
        "service": config.service_name,
        "environment": config.environment,
    }


def _verify_webhook_secret(received: str | None) -> None:
    expected = config.webhook_secret
    if not expected:
        return
    if not received or not secrets.compare_digest(received, expected):
        raise HTTPException(status_code=401, detail="Invalid Telegram webhook secret")


@app.post("/webhooks/telegram")
def telegram_webhook(
    update: dict[str, Any],
    x_telegram_bot_api_secret_token: str | None = Header(
        default=None,
        alias="X-Telegram-Bot-Api-Secret-Token",
    ),
) -> dict[str, str | None]:
    _verify_webhook_secret(x_telegram_bot_api_secret_token)
    return ingest_telegram_update(
        update,
        bot_username=config.telegram_bot_username,
        bot_user_id=config.telegram_bot_user_id,
    ).as_dict()
