"""Bounded operator import of an existing group's Telegram Desktop snapshot."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import logging

from app_v2.adapters.coach_telegram import CoachTelegramClient, CoachTelegramError
from app_v2.adapters.postgres import connect
from app_v2.adapters.telegram_history import parse_telegram_desktop_export
from app_v2.repositories.source_history_import_repo import SourceHistoryImportRepository


logger = logging.getLogger(__name__)


class HistoryImportError(ValueError):
    """Machine reasons only; never includes the private export or credentials."""


def import_history(config, payload: bytes, *, expected_chat_id: int, dry_run: bool = True,
                   client=None, connection_factory=None) -> dict:
    if not isinstance(payload, bytes) or not payload or len(payload) > 8 * 1024 * 1024:
        raise HistoryImportError("invalid_history_payload")
    if type(dry_run) is not bool or type(expected_chat_id) is not int or not -(2**63-1) <= expected_chat_id < 0:
        raise HistoryImportError("invalid_history_import_parameters")
    if not all((config.enabled, config.database_url, config.bot_user_id, config.telegram_bot_token,
                config.scope_configured, config.archive_policy == "until_author_deletes")):
        raise HistoryImportError("history_runtime_not_configured")
    export = parse_telegram_desktop_export(payload, expected_chat_id=expected_chat_id)
    if not export.messages:
        raise HistoryImportError("history_contains_no_importable_messages")
    if any(not config.permits_scope(m.chat_id, m.message_thread_id) for m in export.messages):
        raise HistoryImportError("history_scope_not_allowed")
    owns_client = client is None
    client = client or CoachTelegramClient(config.telegram_bot_token)
    phase = "bot_identity"
    authors = sorted({m.author_user_id for m in export.messages})
    verified_authors = 0
    try:
        identity = client.get_me()
        if (identity.get("id") != config.bot_user_id or str(identity.get("username", "")).casefold()
                != (config.bot_username or "").casefold()):
            raise HistoryImportError("history_bot_identity_mismatch")
        phase = "group_access"
        client.check_group_access(chat_id=export.chat_id, bot_user_id=config.bot_user_id,
                                  can_read_all_group_messages=identity.get("can_read_all_group_messages") is True)
        phase = "author_identity"
        humans = set()
        for author in authors:
            is_human = client.history_author_is_human(chat_id=export.chat_id, user_id=author)
            verified_authors += 1
            if is_human:
                humans.add(author)
    except CoachTelegramError as exc:
        logger.error(
            "Coach history Telegram verification failed: phase=%s verified_authors=%s "
            "total_authors=%s reason=%s method=%s http_status=%s",
            phase, verified_authors, len(authors), exc.reason_code, exc.method, exc.http_status,
        )
        raise
    finally:
        if owns_client:
            client.close()
    nonhuman = sum(m.author_user_id not in humans for m in export.messages)
    if nonhuman:
        export = replace(export, messages=tuple(m for m in export.messages if m.author_user_id in humans),
                         skipped_counts={**export.skipped_counts,
                                         "bot_author": export.skipped_counts.get("bot_author", 0) + nonhuman})
    if not export.messages:
        raise HistoryImportError("history_contains_no_verified_human_messages")
    factory = connection_factory or (lambda: connect(
        config.database_url, autocommit=True, connect_timeout=5,
        options="-c lock_timeout=5000 -c statement_timeout=15000",
    ))
    with factory() as conn:
        with conn.transaction(force_rollback=dry_run):
            counts = SourceHistoryImportRepository(conn, bot_user_id=config.bot_user_id).import_export(export)
    return {"status": "preview" if dry_run else "imported", "dry_run": dry_run,
            "file_sha256": hashlib.sha256(payload).hexdigest(), "counts": counts}
