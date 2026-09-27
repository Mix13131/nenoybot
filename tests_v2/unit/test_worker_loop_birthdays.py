from __future__ import annotations

from app_v2.workers.main import WorkerLoop


class Unit:
    def __init__(self, result=False, error=None):
        self.result=result
        self.error=error
        self.calls=0
    def run_once(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class Periodic:
    def __init__(self, result=None, error=None):
        self.result=result
        self.error=error
        self.calls=0
    def run_if_due(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def test_worker_loop_runs_birthday_scanner_without_starving_core_units():
    event=Unit(False)
    reminder=Unit(False)
    outbox=Unit(True)
    maintenance=Periodic(None)
    birthday=Periodic(True)

    loop=WorkerLoop(
        event,
        reminder,
        outbox,
        maintenance,
        birthday_worker=birthday,
    )

    assert loop.run_once() is True
    assert birthday.calls == 1
    assert event.calls == reminder.calls == outbox.calls == maintenance.calls == 1


def test_birthday_scanner_failure_is_fail_silent_for_worker_loop():
    event=Unit(False)
    reminder=Unit(False)
    outbox=Unit(True)
    maintenance=Periodic(None)
    birthday=Periodic(error=RuntimeError("birthday down"))

    loop=WorkerLoop(
        event,
        reminder,
        outbox,
        maintenance,
        birthday_worker=birthday,
    )

    assert loop.run_once() is True
    assert birthday.calls == 1
    assert outbox.calls == 1
