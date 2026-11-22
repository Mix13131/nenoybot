"""Dedicated coach intake: archive and durable work, no LLM in the webhook.

This path deliberately does not call the general Group/Personal ingestor. Only
the explicitly configured forum topics are accepted; third-party nested reply
objects never become a coach job. All accepted work commits before Telegram ACK.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from app_v2.adapters.telegram_archive import archive_message_from_update
from app_v2.adapters.telegram_webhook import normalize_update
from app_v2.coach_config import CoachConfig
from app_v2.repositories.coach_repo import CoachRepository
from app_v2.repositories.source_archive_repo import SourceArchiveRepository
from app_v2.services.coach_controls import CoachControls


@dataclass(frozen=True)
class CoachIngestResult:
    status: str
    archived: bool = False
    queued: bool = False


def is_coach_addressed(normalized, config: CoachConfig) -> bool:
    message = normalized.message
    text = message.get("text") or message.get("caption") or ""
    if config.bot_username and re.search(r"(?<![\w@])@" + re.escape(config.bot_username) + r"\b", text, re.I):
        return True
    if any(re.match(r"^\s*" + re.escape(alias) + r"(?=$|[\s,:!?.—-])", text, re.I) for alias in config.aliases):
        return True
    reply = message.get("reply_to_message")
    if isinstance(reply, dict):
        author = reply.get("from")
        if isinstance(author, dict) and config.bot_user_id and author.get("id") == config.bot_user_id:
            return True
    return False


def ingest_coach_update(update: dict[str, Any], *, conn, config: CoachConfig) -> CoachIngestResult:
    if not isinstance(update, dict):
        return CoachIngestResult("ignored")
    try:
        normalized = normalize_update(update, bot_username=config.bot_username,
                                      bot_user_id=str(config.bot_user_id) if config.bot_user_id else None)
        message = archive_message_from_update(normalized) if normalized is not None else None
    except (KeyError, TypeError, ValueError, OverflowError):
        return CoachIngestResult("ignored")
    if message is None or not config.permits_scope(message.chat_id, message.message_thread_id):
        return CoachIngestResult("ignored")
    repo = CoachRepository(conn, chat_id=message.chat_id, message_thread_id=message.message_thread_id,
                           bot_user_id=config.bot_user_id, transient_ttl_seconds=config.transient_ttl_seconds)
    archive = SourceArchiveRepository(conn)
    with conn.transaction():
        repo.assert_identity()
        if not repo.claim_ingest(message.telegram_update_id):
            return CoachIngestResult("duplicate")
        scope_enabled = repo.scope_enabled()
        if config.enabled and scope_enabled:
            # The configured group's policy is the default. This is an explicit
            # system policy record, never a fabricated personal opt-in.
            repo.bootstrap_group_default(normalized)
        # Any received edit invalidates ephemeral direct responses even when
        # long-lived archival is off; unrelated unconsented text is not stored.
        if message.is_edited:
            repo.invalidate_message(message)
        # Capture also applies to permitted ordinary messages and commands. It
        # precedes the control/analysis decisions; a subsequent forget in this
        # same transaction removes the command together with the user's archive.
        captured = archive.capture(normalized) if config.enabled else None
        archived = captured is not None and captured.status in {"archived", "duplicate"}
        if not message.is_edited and not message.contains_external_content:
            control = CoachControls(repo, archive_policy=config.archive_policy,
                                    bot_username=config.bot_username, aliases=config.aliases).handle(
                                        normalized, addressed=is_coach_addressed(normalized, config))
            if control.handled:
                if control.reply_text:
                    repo.enqueue_control_output(normalized, control.reply_text, disclosure=control.disclosure)
                queued = False
                if control.job_mode and config.enabled and scope_enabled:
                    queued = repo.enqueue_job(
                        normalized, mode=control.job_mode,
                        source_id=captured.source_id if captured else None,
                        window_days=getattr(control, "window_days", 7),
                        public_confirmed=getattr(control, "public_confirmed", False),
                    ) is not None
                return CoachIngestResult("accepted", archived=archived, queued=queued)
        if not config.enabled or not scope_enabled:
            return CoachIngestResult("disabled")
        if message.is_edited or message.contains_external_content or not message.available_text:
            return CoachIngestResult("accepted", archived=archived)
        permissions = repo.permissions(message.author_user_id)
        mode = None
        if is_coach_addressed(normalized, config):
            if permissions.direct_request_group_reply:
                mode = "direct"
        elif permissions.auto_report_analysis and permissions.unsolicited_group_reply:
            mode = "auto_report"
        elif permissions.retain_source and permissions.historical_inference and permissions.track_status == "active":
            mode = "index"
        # Disabled AI must never prevent capture. Work remains in a short-lived
        # durable queue; the worker explains unavailability for direct requests.
        queued = mode is not None and repo.enqueue_job(normalized, mode=mode, source_id=captured.source_id) is not None
        return CoachIngestResult("accepted", archived=archived, queued=queued)
