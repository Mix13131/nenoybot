from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from app_v2.adapters.postgres import connect
from app_v2.config import load_config
from app_v2.runtime import RuntimeEventHandler, build_runtime
from app_v2.services.maintenance import MaintenanceService
from app_v2.workers.event_worker import EventWorker
from app_v2.workers.maintenance import (
    MaintenanceWorker,
    maintenance_interval_from_env,
    maintenance_policy_from_env,
)
from app_v2.workers.outbox_worker import OutboxWorker
from app_v2.workers.scheduler import ReminderScheduler

logger = logging.getLogger(__name__)


def _float_env(name: str, default: float) -> float:
    value = (os.getenv(name) or "").strip()
    return float(value) if value else default


def _int_env(name: str, default: int) -> int:
    value = (os.getenv(name) or "").strip()
    return int(value) if value else default


@dataclass
class WorkerLoop:
    event_worker: Any
    reminder_scheduler: Any
    outbox_worker: Any
    maintenance_worker: Any

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
    return WorkerLoop(
        event_worker=event_worker,
        reminder_scheduler=reminder_scheduler,
        outbox_worker=outbox_worker,
        maintenance_worker=maintenance_worker,
    )


def run_forever() -> None:
    logging.basicConfig(level=os.getenv("NENOY_V2_LOG_LEVEL", "INFO").upper())
    config = load_config()
    poll_interval = _float_env("NENOY_V2_WORKER_IDLE_SLEEP", 0.5)

    with connect(config.database_url) as conn:
        loop = build_worker_loop(conn, config)
        logger.info("nenoy-v2-worker started")
        while True:
            handled = loop.run_once()
            if not handled:
                time.sleep(poll_interval)


if __name__ == "__main__":
    run_forever()
