from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace

from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.runtime import RuntimeEventHandler
from app_v2.workers.main import WorkerLoop, _configure_logging


def claimed(*, scope="personal", event_type="private_message", event_id="e1"):
    return SimpleNamespace(
        event_id=event_id,
        event_type=event_type,
        scope_type=scope,
        scope_id="u1" if scope == "personal" else "-1001",
        actor_user_id="u1",
        created_at=datetime.now(timezone.utc),
        payload={
            "event_id": event_id,
            "event_type": event_type,
            "scope_type": scope,
            "scope_id": "u1" if scope == "personal" else "-1001",
            "actor_user_id": "u1",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "message_id": "1",
            "text": "hello",
            "metadata": {},
        },
    )


class Calls:
    def __init__(self): self.items=[]
    def process(self, event): self.items.append(event)
    def collect(self, event): self.items.append(event)


def runtime():
    personal=Calls(); group=Calls(); feedback=Calls()
    return SimpleNamespace(personal_pipeline=personal, group_pipeline=group, feedback_collector=feedback), personal, group, feedback


def test_runtime_routes_personal_to_feedback_then_personal_pipeline():
    rt, personal, group, feedback=runtime()
    RuntimeEventHandler(rt)(claimed())
    assert len(feedback.items) == 1
    assert len(personal.items) == 1
    assert group.items == []
    assert personal.items[0].scope_type is ScopeType.PERSONAL


def test_runtime_routes_group_to_group_pipeline_only():
    rt, personal, group, feedback=runtime()
    RuntimeEventHandler(rt)(claimed(scope="group", event_type="group_message"))
    assert len(group.items) == 1
    assert personal.items == []
    assert feedback.items == []


def test_runtime_reaction_is_feedback_transport_not_personal_reply():
    rt, personal, group, feedback=runtime()
    RuntimeEventHandler(rt)(claimed(event_type="reaction_added"))
    assert len(feedback.items) == 1
    assert personal.items == []
    assert group.items == []


class Unit:
    def __init__(self, result=False, error=None):
        self.result=result; self.error=error; self.calls=0
    def run_once(self):
        self.calls += 1
        if self.error: raise self.error
        return self.result


class MaintenanceUnit:
    def __init__(self, result=None, error=None):
        self.result=result; self.error=error; self.calls=0
    def run_if_due(self):
        self.calls += 1
        if self.error: raise self.error
        return self.result


def test_worker_loop_runs_event_reminder_and_outbox_fairly():
    event=Unit(True); reminder=Unit(True); outbox=Unit(True); maintenance=MaintenanceUnit(None)
    loop=WorkerLoop(event, reminder, outbox, maintenance)
    assert loop.run_once() is True
    assert event.calls == reminder.calls == outbox.calls == maintenance.calls == 1


def test_maintenance_failure_does_not_block_delivery_units():
    event=Unit(False); reminder=Unit(False); outbox=Unit(True)
    maintenance=MaintenanceUnit(error=RuntimeError("maintenance down"))
    loop=WorkerLoop(event, reminder, outbox, maintenance)
    assert loop.run_once() is True
    assert event.calls == 1
    assert reminder.calls == 1
    assert outbox.calls == 1


def test_transport_loggers_are_warning_or_higher(monkeypatch):
    monkeypatch.setenv("NENOY_V2_LOG_LEVEL", "INFO")
    for name in ("httpx", "httpx2", "httpcore", "httpcore2"):
        logging.getLogger(name).setLevel(logging.INFO)

    _configure_logging()

    for name in ("httpx", "httpx2", "httpcore", "httpcore2"):
        assert logging.getLogger(name).level >= logging.WARNING
