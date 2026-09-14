from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app_v2.services.maintenance import MaintenancePolicy, MaintenanceReport, MaintenanceService


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    value = int(raw)
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def maintenance_policy_from_env() -> MaintenancePolicy:
    return MaintenancePolicy(
        warm_retention_days=_int_env("NENOY_V2_WARM_RETENTION_DAYS", 30),
        terminal_retention_days=_int_env("NENOY_V2_TERMINAL_RETENTION_DAYS", 30),
        personal_memory_soft_cap=_int_env("NENOY_V2_PERSONAL_MEMORY_SOFT_CAP", 150),
        group_memory_soft_cap=_int_env("NENOY_V2_GROUP_MEMORY_SOFT_CAP", 250),
    )


@dataclass
class MaintenanceWorker:
    service: MaintenanceService
    interval_seconds: int = 900
    last_run_at: datetime | None = None

    def run_if_due(self, *, now: datetime | None = None) -> MaintenanceReport | None:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if self.last_run_at is not None:
            next_run = self.last_run_at + timedelta(seconds=self.interval_seconds)
            if current < next_run:
                return None
        report = self.service.run(now=current)
        self.last_run_at = current
        return report


def maintenance_interval_from_env() -> int:
    return _int_env("NENOY_V2_MAINTENANCE_INTERVAL_SECONDS", 900, minimum=60)
