"""Dedicated FastAPI entrypoint: uvicorn app_v2.coach_main:app.

Importing the module never sets a Telegram webhook, migrates a DB or contacts
an AI provider. Operations are explicit in coach_admin and the separate worker.
"""
from __future__ import annotations

import json
import logging
import secrets
from typing import Callable

from fastapi import FastAPI, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app_v2.adapters.postgres import connect
from app_v2.coach_config import CoachConfig, load_coach_config
from app_v2.repositories.coach_scope_repo import CoachScopeRepository
from app_v2.services.coach_ingestor import ingest_coach_update


logger = logging.getLogger(__name__)
MAX_UPDATE_BYTES = 1024 * 1024


def create_app(config: CoachConfig | None = None, *, connection_factory: Callable | None = None) -> FastAPI:
    config = config or load_coach_config()
    factory = connection_factory or (lambda: connect(
        config.database_url, autocommit=True, connect_timeout=5,
        options="-c lock_timeout=5000 -c statement_timeout=15000",
    ))
    application = FastAPI(title=config.display_name)

    @application.get("/health")
    def health():
        return {"status": "ok", "service": "coach-web", "enabled": config.enabled}

    @application.get("/ready")
    def ready():
        if not config.database_url or not config.bot_user_id or not config.scope_configured:
            raise HTTPException(503, "Coach is not configured")
        try:
            with factory() as conn:
                CoachScopeRepository(conn, bot_user_id=config.bot_user_id).assert_identity()
                conn.execute("SELECT 1 FROM coach_group_memberships LIMIT 0")
                conn.execute("SELECT 1 FROM coach_jobs LIMIT 0")
                conn.execute("SELECT 1 FROM source_archive_revisions LIMIT 0")
                conn.execute("SELECT 1 FROM coach_dialogue_turns LIMIT 0")
                conn.execute("SELECT grant_basis FROM source_archive_consents LIMIT 0")
                conn.execute("SELECT bundle_id,part_index FROM coaching_outputs LIMIT 0")
                if config.ai_enabled:
                    conn.execute("SELECT budget_mode,cap_microusd FROM coach_budget_days LIMIT 0")
                    conn.execute("SELECT 1 FROM coach_ai_operations LIMIT 0")
        except Exception:
            raise HTTPException(503, "Coach database is not ready") from None
        return {"status": "ready", "service": "coach-web", "enabled": config.enabled,
                "ai_enabled": config.ai_enabled}

    @application.post("/webhooks/telegram")
    async def webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(
        default=None, alias="X-Telegram-Bot-Api-Secret-Token",
    )):
        if not config.webhook_secret:
            raise HTTPException(503, "Coach is not configured")
        if not x_telegram_bot_api_secret_token or not secrets.compare_digest(
            x_telegram_bot_api_secret_token.encode("utf-8"), config.webhook_secret.encode("utf-8"),
        ):
            raise HTTPException(401, "Invalid webhook secret")
        chunks = bytearray()
        async for chunk in request.stream():
            if len(chunks) + len(chunk) > MAX_UPDATE_BYTES:
                raise HTTPException(413, "Update too large")
            chunks.extend(chunk)
        try:
            update = json.loads(chunks)
        except (UnicodeDecodeError, ValueError):
            raise HTTPException(400, "Invalid JSON") from None
        if not isinstance(update, dict):
            raise HTTPException(400, "Expected update object")
        try:
            def commit_update():
                with factory() as conn:
                    return ingest_coach_update(update, conn=conn, config=config)
            result = await run_in_threadpool(commit_update)
        except Exception as exc:
            # A 503 asks Telegram to retry the uncommitted update. No raw data or
            # exception traceback is logged; DB errors can include private rows.
            logger.error("Coach ingest failed: %s", type(exc).__name__)
            raise HTTPException(503, "Update not committed; retry required") from None
        return {"status": result.status}

    return application


app = create_app()
