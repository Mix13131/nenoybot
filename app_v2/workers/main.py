from __future__ import annotations

import os
import time

from app_v2.adapters.postgres import connect
from app_v2.repositories.event_repo import EventRepository
from app_v2.workers.event_worker import EventWorker


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
        worker = EventWorker(
            EventRepository(conn),
            processing_timeout_seconds=processing_timeout,
            max_attempts=max_attempts,
            base_delay_seconds=base_delay,
            max_delay_seconds=max_delay,
        )
        while True:
            processed = worker.run_once()
            if not processed:
                time.sleep(poll_interval)


if __name__ == "__main__":
    run_forever()
