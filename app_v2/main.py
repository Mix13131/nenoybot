from __future__ import annotations

import logging
import secrets
from typing import Any

from fastapi import FastAPI, Header, HTTPException

from .adapters.postgres import connect
from .config import AppConfig, load_config
from .services.event_ingestor import ingest_telegram_update
from .telegram_webhook_setup import (
    TelegramWebhookSetupError,
    configure_webhook,
    resolve_webhook_url,
)


logger = logging.getLogger(__name__)
config: AppConfig = load_config()


def _configure_telegram_webhook_on_boot() -> None:
    """Idempotently bind the current Telegram token to this web service.

    Token rotation invalidates the old Bot API credential and therefore the
    webhook must be registered again with the new token. Doing it on web boot
    keeps that invariant automatic instead of relying on a manual one-off CLI.
    """
    if config.environment != "production":
        return
    if not config.telegram_bot_token or not config.webhook_secret:
        return

    try:
        webhook_url = resolve_webhook_url()
        info = configure_webhook(
            token=config.telegram_bot_token,
            secret=config.webhook_secret,
            webhook_url=webhook_url,
        )
    except TelegramWebhookSetupError as exc:
        # The error class is intentionally sanitized: never include token or
        # webhook secret in this log line.
        logger.error("Telegram webhook boot configuration failed: %s", exc)
        return

    logger.info(
        "Telegram webhook ready url=%s pending_update_count=%s last_error_present=%s",
        info.url,
        info.pending_update_count,
        bool(info.last_error_message),
    )


_configure_telegram_webhook_on_boot()
app = FastAPI(title=config.app_name)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": config.service_name,
    }


@app.get("/ready")
def ready() -> dict[str, str]:
    # Development/test can intentionally run without a real DB. Production
    # config cannot, so Railway readiness becomes a real dependency check.
    database_status = "not_configured"
    if config.database_url:
        try:
            with connect(config.database_url, connect_timeout=3) as conn:
                row = conn.execute("SELECT 1").fetchone()
                if not row or int(row[0]) != 1:
                    raise RuntimeError("unexpected PostgreSQL readiness result")
            database_status = "ok"
        except Exception as exc:
            raise HTTPException(status_code=503, detail="PostgreSQL not ready") from exc

    return {
        "status": "ready",
        "service": config.service_name,
        "environment": config.environment,
        "database": database_status,
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
    ingest_kwargs: dict[str, Any] = {
        "bot_username": config.telegram_bot_username,
        "bot_user_id": config.telegram_bot_user_id,
    }
    # Preserve the existing call contract in local/test mode; production has an
    # explicit DB URL and passes it through rather than relying on ambient env.
    if config.database_url:
        ingest_kwargs["database_url"] = config.database_url
    return ingest_telegram_update(update, **ingest_kwargs).as_dict()
