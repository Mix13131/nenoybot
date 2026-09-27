from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app_v2.workers.birthdays import BirthdayWorker


class Service:
    def __init__(self, result=False):
        self.result=result
        self.calls=[]
    def run_once(self, *, now=None):
        self.calls.append(now)
        return self.result


def test_birthday_worker_respects_scan_interval():
    service=Service(True)
    worker=BirthdayWorker(service, interval_seconds=60)
    now=datetime(2026,9,27,6,0,tzinfo=timezone.utc)

    assert worker.run_if_due(now=now) is True
    assert worker.run_if_due(now=now+timedelta(seconds=30)) is None
    assert worker.run_if_due(now=now+timedelta(seconds=60)) is True
    assert len(service.calls) == 2


def test_birthday_worker_requires_timezone_aware_now():
    service=Service()
    worker=BirthdayWorker(service)

    try:
        worker.run_if_due(now=datetime(2026,9,27,6,0))
    except ValueError as exc:
        assert "timezone-aware" in str(exc)
    else:
        raise AssertionError("expected ValueError")
