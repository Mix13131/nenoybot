from __future__ import annotations

import secrets
from typing import Any

from fastapi import FastAPI, Header, HTTPException

from .adapters.postgres import connect
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
