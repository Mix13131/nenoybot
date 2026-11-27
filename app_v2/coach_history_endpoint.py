"""Explicit, temporary HTTPS transport for an operator-provided history export.

The route is closed unless all four settings are valid. It is deliberately
separate from Telegram updates: an export is never presented as a webhook.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import os
import re
import secrets
from typing import Callable, Mapping

from fastapi import FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app_v2.adapters.coach_telegram import CoachTelegramError
from app_v2.coach_config import CoachConfig


logger = logging.getLogger(__name__)
MAX_HISTORY_BYTES = 8 * 1024 * 1024
_MAX_ID = (1 << 63) - 1
_COUNTERS = frozenset({
    "input_events", "candidates", "topics", "authors", "archived",
    "duplicates", "deleted", "permission_skipped", "external_content",
    "partial_bodies", "available_sources", "analysis_jobs_created",
    "telegram_messages_sent",
})


@dataclass(frozen=True)
class _ImportSettings:
    secret: str
    sha256: str
    chat_id: int
    expires_at: datetime


def _settings(environment: Mapping[str, str]) -> _ImportSettings | None:
    """Malformed or incomplete opt-in configuration keeps the route closed."""
    secret = environment.get("COACH_HISTORY_IMPORT_SECRET", "")
    digest = environment.get("COACH_HISTORY_IMPORT_SHA256", "")
    chat = environment.get("COACH_HISTORY_IMPORT_CHAT_ID", "")
    expiry = environment.get("COACH_HISTORY_IMPORT_EXPIRES_AT", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", secret):
        return None
    if not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
        return None
    if not re.fullmatch(r"-[1-9][0-9]{0,18}", chat):
        return None
    chat_id = int(chat)
    if not -_MAX_ID <= chat_id < 0:
        return None
    try:
        expires_at = datetime.fromisoformat(expiry)
    except ValueError:
        return None
    if expires_at.utcoffset() != timedelta(0):
        return None
    return _ImportSettings(secret, digest.lower(), chat_id, expires_at)


def import_history(config: CoachConfig, payload: bytes, **kwargs) -> dict:
    """Load the importer only for an authenticated, digest-matched request."""
    from app_v2.services.coach_history_import import import_history as perform_import
    return perform_import(config, payload, **kwargs)


def _safe_result(result: object, *, settings: _ImportSettings, dry_run: bool) -> dict:
    """Never return incidental source data from the service's result object."""
    status = "preview" if dry_run else "imported"
    if not isinstance(result, dict) or (
        result.get("status") != status
        or result.get("file_sha256") != settings.sha256
        or result.get("dry_run") is not dry_run
        or not isinstance(result.get("counts"), dict)
    ):
        raise RuntimeError("Invalid history import receipt")
    counts = {}
    for key, value in result["counts"].items():
        if not isinstance(key, str) or not (
            key in _COUNTERS or re.fullmatch(r"skipped_[a-z_]{1,48}", key)
        ) or type(value) is not int or value < 0:
            raise RuntimeError("Invalid history import counters")
        counts[key] = value
    return {
        "status": status, "file_sha256": settings.sha256,
        "dry_run": dry_run, "counts": counts,
    }


def register_history_import(
    application: FastAPI, config: CoachConfig, connection_factory: Callable,
) -> None:
    settings = _settings(os.environ)

    @application.post("/admin/history-import", include_in_schema=False)
    async def history_import(request: Request):
        if settings is None or datetime.now(timezone.utc) >= settings.expires_at:
            raise HTTPException(404, "Not found")
        supplied = request.headers.get("X-Coach-Import-Secret", "")
        if not supplied or not secrets.compare_digest(
            supplied.encode("utf-8"), settings.secret.encode("utf-8"),
        ):
            raise HTTPException(401, "Invalid import authorization")
        modes = request.query_params.getlist("apply")
        if len(modes) > 1 or (modes and modes[0] not in {"true", "false"}):
            raise HTTPException(400, "Invalid import mode")
        dry_run = not modes or modes[0] == "false"
        declared_length = request.headers.get("Content-Length")
        if declared_length is not None:
            if not re.fullmatch(r"[0-9]{1,20}", declared_length):
                raise HTTPException(400, "Invalid content length")
            if int(declared_length) > MAX_HISTORY_BYTES:
                raise HTTPException(413, "History export too large")
        chunks = bytearray()
        try:
            async for chunk in request.stream():
                if len(chunks) + len(chunk) > MAX_HISTORY_BYTES:
                    raise HTTPException(413, "History export too large")
                chunks.extend(chunk)
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("Coach history import read failed: %s", type(exc).__name__)
            raise HTTPException(400, "History export could not be read") from None
        payload = bytes(chunks)
        if not secrets.compare_digest(hashlib.sha256(payload).hexdigest(), settings.sha256):
            raise HTTPException(400, "History export digest mismatch")
        # An upload that outlives its explicit access window must not be applied.
        if datetime.now(timezone.utc) >= settings.expires_at:
            raise HTTPException(404, "Not found")
        try:
            result = await run_in_threadpool(
                import_history, config, payload, expected_chat_id=settings.chat_id,
                dry_run=dry_run, connection_factory=connection_factory,
            )
            receipt = _safe_result(result, settings=settings, dry_run=dry_run)
        except ValueError as exc:
            logger.warning("Coach history import rejected: %s", type(exc).__name__)
            raise HTTPException(400, "History import rejected") from None
        except CoachTelegramError as exc:
            logger.error(
                "Coach history import failed: %s reason=%s method=%s http_status=%s",
                type(exc).__name__, exc.reason_code, exc.method, exc.http_status,
            )
            raise HTTPException(503, "History import unavailable") from None
        except Exception as exc:
            logger.error("Coach history import failed: %s", type(exc).__name__)
            raise HTTPException(503, "History import unavailable") from None
        logger.info(
            "Coach history import %s: file_sha256=%s counts=%s",
            receipt["status"], receipt["file_sha256"], receipt["counts"],
        )
        return receipt
