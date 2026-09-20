from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app_v2.workers.silence_wakeup import GroupSilenceWakeupWorker


class Service:
    def __init__(self, result=False):
        self.result = result
        self.calls = []

    def run_once(self, *, now):
        self.calls.append(now)
        return self.result


def test_silence_wakeup_worker_runs_immediately_then_once_per_interval():
    svc = Service(result=True)
    worker = GroupSilenceWakeupWorker(svc, interval_seconds=60)
    now = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)

    assert worker.run_if_due(now=now) is True
    assert worker.run_if_due(now=now + timedelta(seconds=59)) is None
    assert worker.run_if_due(now=now + timedelta(seconds=60)) is True
    assert len(svc.calls) == 2


def test_silence_wakeup_worker_rejects_naive_time():
    worker = GroupSilenceWakeupWorker(Service())
    with pytest.raises(ValueError, match="timezone-aware"):
        worker.run_if_due(now=datetime(2026, 9, 20, 10, 0))
