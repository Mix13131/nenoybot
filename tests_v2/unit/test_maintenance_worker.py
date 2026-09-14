from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app_v2.services.maintenance import MaintenanceReport
from app_v2.workers.maintenance import (
    MaintenanceWorker,
    maintenance_interval_from_env,
    maintenance_policy_from_env,
)


class FakeService:
    def __init__(self):
        self.calls = []

    def run(self, *, now):
        self.calls.append(now)
        return MaintenanceReport(raw_messages_expired=1)


def test_worker_runs_immediately_then_waits_until_interval():
    service = FakeService()
    worker = MaintenanceWorker(service, interval_seconds=900)
    now = datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc)

    first = worker.run_if_due(now=now)
    early = worker.run_if_due(now=now + timedelta(seconds=899))
    second = worker.run_if_due(now=now + timedelta(seconds=900))

    assert first is not None and first.raw_messages_expired == 1
    assert early is None
    assert second is not None
    assert len(service.calls) == 2


def test_worker_rejects_naive_time():
    worker = MaintenanceWorker(FakeService())
    with pytest.raises(ValueError, match="timezone-aware"):
        worker.run_if_due(now=datetime(2026, 9, 14, 8, 0))


def test_policy_env_overrides(monkeypatch):
    monkeypatch.setenv("NENOY_V2_WARM_RETENTION_DAYS", "14")
    monkeypatch.setenv("NENOY_V2_TERMINAL_RETENTION_DAYS", "45")
    monkeypatch.setenv("NENOY_V2_PERSONAL_MEMORY_SOFT_CAP", "120")
    monkeypatch.setenv("NENOY_V2_GROUP_MEMORY_SOFT_CAP", "220")
    monkeypatch.setenv("NENOY_V2_MAINTENANCE_INTERVAL_SECONDS", "600")

    policy = maintenance_policy_from_env()
    assert policy.warm_retention_days == 14
    assert policy.terminal_retention_days == 45
    assert policy.personal_memory_soft_cap == 120
    assert policy.group_memory_soft_cap == 220
    assert maintenance_interval_from_env() == 600


def test_maintenance_interval_has_safe_minimum(monkeypatch):
    monkeypatch.setenv("NENOY_V2_MAINTENANCE_INTERVAL_SECONDS", "30")
    with pytest.raises(ValueError, match=">= 60"):
        maintenance_interval_from_env()
