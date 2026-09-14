from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app_v2.domain.actions import ActionRequest
from app_v2.domain.enums import ScopeType
from app_v2.services.action_engine import ActionEngine, ActionEngineError
from app_v2.workers.scheduler import ReminderScheduler


class FakeTasks:
    def __init__(self): self.rows={}; self.next_id=1
    def create(self, **kwargs):
        row=SimpleNamespace(id=self.next_id,status='open',title=kwargs['title'],due_at=kwargs.get('due_at')); self.rows[row.id]=row; self.next_id+=1; return row
    def update(self, task_id, **kwargs):
        row=self.rows.get(task_id)
        if not row: return None
        if kwargs.get('title'): row.title=kwargs['title']
        return row
    def complete(self, task_id):
        row=self.rows.get(task_id)
        if not row: return False
        row.status='completed'; return True


class FakeReminders:
    def __init__(self): self.rows={}; self.next_id=1; self.fire_count=0
    def create(self, **kwargs):
        if kwargs.get('recurrence_rule'): raise NotImplementedError
        row=SimpleNamespace(id=self.next_id,status='pending',due_at=kwargs['due_at']); self.rows[row.id]=row; self.next_id+=1; return row
    def cancel(self, reminder_id):
        row=self.rows.get(reminder_id)
        if not row: return False
        row.status='cancelled'; return True
    def reschedule(self, reminder_id, due_at):
        row=self.rows.get(reminder_id)
        if not row: return None
        row.due_at=due_at; row.status='pending'; return row
    def fire_due_once(self):
        self.fire_count += 1
        return None


def request(action, payload):
    return ActionRequest(action_type=action,scope_type=ScopeType.PERSONAL,scope_id='u1',actor_user_id='u1',payload=payload,requested_at=datetime.now(timezone.utc))


def test_create_update_complete_task():
    tasks=FakeTasks(); engine=ActionEngine(task_repo=tasks,reminder_repo=FakeReminders())
    created=engine.execute(request('create_task',{'title':'Сделать отчёт'}))
    updated=engine.execute(request('update_task',{'task_id':created.entity_id,'title':'Отправить отчёт'}))
    completed=engine.execute(request('complete_task',{'task_id':created.entity_id}))
    assert created.changed and updated.changed and completed.changed
    assert tasks.rows[created.entity_id].status == 'completed'
    assert tasks.rows[created.entity_id].title == 'Отправить отчёт'


def test_create_cancel_reschedule_reminder():
    reminders=FakeReminders(); engine=ActionEngine(task_repo=FakeTasks(),reminder_repo=reminders)
    due=datetime.now(timezone.utc)+timedelta(hours=1)
    created=engine.execute(request('create_reminder',{'due_at':due.isoformat(),'text':'Позвонить'}))
    later=due+timedelta(hours=2)
    moved=engine.execute(request('reschedule_reminder',{'reminder_id':created.entity_id,'due_at':later.isoformat()}))
    cancelled=engine.execute(request('cancel_reminder',{'reminder_id':created.entity_id}))
    assert created.changed and moved.changed and cancelled.changed
    assert reminders.rows[created.entity_id].status == 'cancelled'
    assert reminders.rows[created.entity_id].due_at == later


def test_naive_datetime_is_rejected():
    engine=ActionEngine(task_repo=FakeTasks(),reminder_repo=FakeReminders())
    with pytest.raises(ActionEngineError, match='timezone-aware'):
        engine.execute(request('create_reminder',{'due_at':'2026-09-14T10:00:00','text':'x'}))


def test_recurring_reminder_is_explicitly_not_supported():
    engine=ActionEngine(task_repo=FakeTasks(),reminder_repo=FakeReminders())
    with pytest.raises(NotImplementedError):
        engine.execute(request('create_reminder',{'due_at':datetime.now(timezone.utc).isoformat(),'recurrence_rule':'FREQ=DAILY'}))


def test_unknown_action_rejected():
    engine=ActionEngine(task_repo=FakeTasks(),reminder_repo=FakeReminders())
    with pytest.raises(ActionEngineError, match='Unsupported'):
        engine.execute(request('launch_rocket',{}))


def test_scheduler_run_once_is_thin_restart_safe_db_loop():
    reminders=FakeReminders(); scheduler=ReminderScheduler(reminders)
    assert scheduler.run_once() is False
    assert reminders.fire_count == 1
