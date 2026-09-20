from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app_v2.adapters.postgres import connect
from app_v2.config import load_config
from app_v2.db.migrations import run_migrations
from app_v2.group_admin import FRIENDS_DAY1_PROFILE
from app_v2.repositories.group_context_repo import GroupContextRepository
from app_v2.repositories.group_initiative_repo import GroupInitiativeRepository
from app_v2.runtime import RuntimeEventHandler, build_runtime
from app_v2.services.group_initiative import GroupInitiativeService
from app_v2.services.group_silence_wakeup import GroupSilenceWakeupService
from app_v2.services.maintenance import MaintenanceService
from app_v2.workers.event_worker import EventWorker
from app_v2.workers.maintenance import (
    MaintenanceWorker,
    maintenance_interval_from_env,
    maintenance_policy_from_env,
)
from app_v2.workers.outbox_worker import OutboxWorker
from app_v2.workers.scheduler import ReminderScheduler
from app_v2.workers.silence_wakeup import GroupSilenceWakeupWorker

logger = logging.getLogger(__name__)


def _float_env(name: str, default: float) -> float:
    value = (os.getenv(name) or "").strip()
    return float(value) if value else default


def _int_env(name: str, default: int) -> int:
    value = (os.getenv(name) or "").strip()
    return int(value) if value else default


def _configure_logging() -> None:
    """Configure runtime logging without leaking credential-bearing request URLs."""
    logging.basicConfig(level=os.getenv("NENOY_V2_LOG_LEVEL", "INFO").upper())
    # Telegram Bot API embeds the bot token in the request URL. Both the
    # Telegram sender (httpx) and OpenAI SDK (httpx2) may log full request URLs
    # at INFO, so keep transport libraries at WARNING or above.
    for name in ("httpx", "httpx2", "httpcore", "httpcore2"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _truthy_env(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _activate_group(repo: GroupContextRepository, match, *, reason: str) -> dict[str, str] | None:
    changed = repo.configure_friends_test(
        match.telegram_chat_id,
        profile=FRIENDS_DAY1_PROFILE,
        enabled=True,
    )
    if not changed:
        logger.error(
            "group bootstrap failed reason=%s title=%r telegram_chat_id=%s",
            reason,
            match.title,
            match.telegram_chat_id,
        )
        return None

    logger.warning(
        "group bootstrap activated reason=%s title=%r telegram_chat_id=%s",
        reason,
        match.title,
        match.telegram_chat_id,
    )
    return {
        "title": str(match.title or ""),
        "telegram_chat_id": str(match.telegram_chat_id),
    }


def _bootstrap_group_from_env(conn, *, now: datetime | None = None) -> dict[str, str] | None:
    """Optionally activate exactly one previously ingested group.

    Preferred mode is an exact title match. For controlled onboarding after a
    fresh Telegram update, ops can instead request the single recent active,
    non-whitelisted group. Both modes fail closed on zero or multiple matches.
    Bootstrap env vars are temporary and should be cleared after use.
    """

    title = (os.getenv("NENOY_V2_BOOTSTRAP_GROUP_TITLE") or "").strip()
    recent = _truthy_env("NENOY_V2_BOOTSTRAP_RECENT_UNWHITELISTED")
    if not title and not recent:
        return None

    repo = GroupContextRepository(conn)
    rows = repo.list_groups(limit=100)

    if title:
        matches = [row for row in rows if (row.title or "").strip() == title]
        if not matches:
            logger.warning("group bootstrap skipped: title not found title=%r", title)
            return None
        if len(matches) != 1:
            logger.error(
                "group bootstrap skipped: ambiguous title=%r matches=%d",
                title,
                len(matches),
            )
            return None
        return _activate_group(repo, matches[0], reason="exact_title")

    minutes = _int_env("NENOY_V2_BOOTSTRAP_RECENT_MINUTES", 60)
    minutes = max(1, min(minutes, 180))
    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(minutes=minutes)

    matches = []
    for row in rows:
        updated_at = row.updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        if (
            not row.is_whitelisted
            and row.is_active
            and updated_at >= cutoff
        ):
            matches.append(row)

    if not matches:
        logger.warning(
            "group bootstrap skipped: no recent unwhitelisted group within %d minutes",
            minutes,
        )
        return None
    if len(matches) != 1:
        logger.error(
            "group bootstrap skipped: recent unwhitelisted group is ambiguous matches=%d window_minutes=%d",
            len(matches),
            minutes,
        )
        return None

    return _activate_group(repo, matches[0], reason="recent_unwhitelisted")


@dataclass
class WorkerLoop:
    event_worker: Any
    reminder_scheduler: Any
    outbox_worker: Any
    maintenance_worker: Any
    silence_wakeup_worker: Any | None = None

    def run_once(self) -> bool:
        """Run one fair cycle; return True when any durable work was handled."""
        handled = False
        try:
            report = self.maintenance_worker.run_if_due()
            handled = handled or report is not None
        except Exception:
            # Maintenance is best-effort housekeeping. A failure here must not
            # stop replies/reminders/outbound delivery.
            logger.exception("v2 maintenance cycle failed")

        if self.silence_wakeup_worker is not None:
            try:
                wakeup = self.silence_wakeup_worker.run_if_due()
                handled = handled or bool(wakeup)
            except Exception:
                # Re-engagement is optional product behavior. Any failure must
                # degrade to silence and never block normal Telegram handling.
                logger.exception("v2 silence wakeup cycle failed")

        handled = self.event_worker.run_once() or handled
        handled = self.reminder_scheduler.run_once() or handled
        handled = self.outbox_worker.run_once() or handled
        return handled


def build_worker_loop(conn, config=None) -> WorkerLoop:
    cfg = config or load_config()
    runtime = build_runtime(conn, cfg)

    event_worker = EventWorker(
        runtime.event_repo,
        handler=RuntimeEventHandler(runtime),
        processing_timeout_seconds=_float_env("NENOY_V2_EVENT_PROCESSING_TIMEOUT", 120.0),
        max_attempts=_int_env("NENOY_V2_EVENT_MAX_ATTEMPTS", 5),
        base_delay_seconds=_float_env("NENOY_V2_EVENT_RETRY_BASE_DELAY", 5.0),
        max_delay_seconds=_float_env("NENOY_V2_EVENT_RETRY_MAX_DELAY", 300.0),
    )
    reminder_scheduler = ReminderScheduler(
        runtime.reminder_repo,
        poll_interval_seconds=_float_env("NENOY_V2_REMINDER_POLL_INTERVAL", 5.0),
    )
    outbox_worker = OutboxWorker(
        runtime.outbox_repo,
        runtime.telegram_sender,
        processing_timeout_seconds=_float_env("NENOY_V2_OUTBOX_PROCESSING_TIMEOUT", 120.0),
        max_attempts=_int_env("NENOY_V2_OUTBOX_MAX_ATTEMPTS", 5),
        base_delay_seconds=_float_env("NENOY_V2_OUTBOX_RETRY_BASE_DELAY", 5.0),
        max_delay_seconds=_float_env("NENOY_V2_OUTBOX_RETRY_MAX_DELAY", 300.0),
    )
    maintenance_worker = MaintenanceWorker(
        MaintenanceService(
            maintenance_repo=runtime.maintenance_repo,
            memory_repo=runtime.memory_repo,
            event_repo=runtime.event_repo,
            outbox_repo=runtime.outbox_repo,
            policy=maintenance_policy_from_env(),
        ),
        interval_seconds=maintenance_interval_from_env(),
    )
    silence_wakeup_worker = GroupSilenceWakeupWorker(
        GroupSilenceWakeupService(
            repo=runtime.silence_wakeup_repo,
            group_context_repo=GroupContextRepository(conn),
            initiative_service=GroupInitiativeService(
                GroupInitiativeRepository(conn)
            ),
        ),
        interval_seconds=max(
            60,
            _int_env("NENOY_V2_SILENCE_WAKEUP_SCAN_SECONDS", 60),
        ),
    )
    return WorkerLoop(
        event_worker=event_worker,
        reminder_scheduler=reminder_scheduler,
        outbox_worker=outbox_worker,
        maintenance_worker=maintenance_worker,
        silence_wakeup_worker=silence_wakeup_worker,
    )


def run_forever() -> None:
    _configure_logging()
    config = load_config()
    poll_interval = _float_env("NENOY_V2_WORKER_IDLE_SLEEP", 0.5)

    applied_migrations = run_migrations(config.database_url)
    logger.info(
        "database migrations ready applied=%s",
        ",".join(f"{version:04d}" for version in applied_migrations) or "none",
    )

    with connect(config.database_url) as conn:
        _bootstrap_group_from_env(conn)
        loop = build_worker_loop(conn, config)
        logger.info("nenoy-v2-worker started")
        while True:
            handled = loop.run_once()
            if not handled:
                time.sleep(poll_interval)


if __name__ == "__main__":
    run_forever()
