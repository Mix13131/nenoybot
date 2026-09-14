from __future__ import annotations

import logging
import os
import time

from app_v2.adapters.postgres import connect
from app_v2.repositories.event_repo import EventRepository
from app_v2.repositories.maintenance_repo import MaintenanceRepository
from app_v2.repositories.memory_repo import MemoryRepository
from app_v2.repositories.outbox_repo import OutboxRepository
from app_v2.services.maintenance import MaintenanceService
from app_v2.workers.event_worker import EventWorker
from app_v2.workers.maintenance import (
    MaintenanceWorker,
    maintenance_interval_from_env,
    maintenance_policy_from_env,
)

logger = logging.getLogger(__name__)


def _float_env(name: str, default: float) -> float:
    value = (os.getenv(name) or "").strip()
    return float(value) if value else default


def _int_env(name: str, default: int) -> int:
    value = (os.getenv(name) or "").strip()
    return int(value) if value else default


def run_forever() -> None:
    poll_interval = _float_env("NENOY_V2_EVENT_POLL_INTERVAL", 1.0)
    processing_timeout = _float_env("NENOY_V2_EVENT_PROCESSING_TIMEOUT", 120.0)
    max_attempts = _int_env("NENOY_V2_EVENT_MAX_ATTEMPTS", 5)
    base_delay = _float_env("NENOY_V2_EVENT_RETRY_BASE_DELAY", 5.0)
    max_delay = _float_env("NENOY_V2_EVENT_RETRY_MAX_DELAY", 300.0)

    with connect() as conn:
        event_repo = EventRepository(conn)
        worker = EventWorker(
            event_repo,
            processing_timeout_seconds=processing_timeout,
            max_attempts=max_attempts,
            base_delay_seconds=base_delay,
            max_delay_seconds=max_delay,
        )
        maintenance_worker = MaintenanceWorker(
            MaintenanceService(
                maintenance_repo=MaintenanceRepository(conn),
                memory_repo=MemoryRepository(conn),
                event_repo=event_repo,
                outbox_repo=OutboxRepository(conn),
                policy=maintenance_policy_from_env(),
            ),
            interval_seconds=maintenance_interval_from_env(),
        )

        while True:
            try:
                maintenance_worker.run_if_due()
            except Exception:
                # Maintenance failure must not stop normal event processing.
                logger.exception("v2 maintenance cycle failed")

            processed = worker.run_once()
            if not processed:
                time.sleep(poll_interval)


if __name__ == "__main__":
    run_forever()
