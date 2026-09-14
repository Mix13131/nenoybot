from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.services.group_reminders import GroupReminderService


class FakeReminderRepo:
    def __init__(self):
        self.created = []
        self.cancel_calls = []
        self.manual_cancel_calls = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id=17, due_at=kwargs["due_at"])

    def cancel_waiting_for_response(self, **kwargs):
        self.cancel_calls.append(kwargs)
        return 2

    def cancel_group_reminders(self, **kwargs):
        self.manual_cancel_calls.append(kwargs)
        return 1


def make_event(*, text, metadata=None, event_type=EventType.REPLY_TO_BOT, actor="101"):
    return EventEnvelope(
        event_id="tg:1",
        event_type=event_type,
        occurred_at=datetime(2026, 9, 14, 15, 0, tzinfo=timezone.utc),
        scope_type=ScopeType.GROUP,
        scope_id="-1001",
        actor_user_id=actor,
        message_id="55",
        reply_to_message_id="54",
        text=text,
        metadata=metadata or {},
    )


def test_recurring_half_hour_reminder_until_reply():
    repo = FakeReminderRepo()
    service = GroupReminderService(repo)
    now = datetime(2026, 9, 14, 15, 10, tzinfo=timezone.utc)
    item = make_event(
        text="напоминай об этом каждые полчаса, пока он не ответит",
        metadata={"reply_to_text": "@toroikin, завтра баня?", "message_thread_id": 777},
    )
    result = service.maybe_schedule(item, now=now)
    assert result.status == "scheduled"
    assert result.interval_seconds == 1800
    assert result.target_username == "toroikin"
    assert result.due_at == now + timedelta(minutes=30)
    assert repo.created[0]["recurrence_rule"] == "interval:1800"
    assert repo.created[0]["payload"]["message_thread_id"] == 777


def test_target_reply_cancels_waiting_reminders():
    repo = FakeReminderRepo()
    service = GroupReminderService(repo)
    item = make_event(
        text="Да",
        metadata={"actor_username": "toroikin"},
        event_type=EventType.GROUP_MESSAGE,
        actor="202",
    )
    assert service.cancel_on_response(item) == 2
    assert repo.cancel_calls[0]["actor_username"] == "toroikin"


def test_one_shot_relative_reminder():
    repo = FakeReminderRepo()
    service = GroupReminderService(repo)
    now = datetime(2026, 9, 14, 15, 10, tzinfo=timezone.utc)
    item = make_event(
        text="НеНой, напомни через 15 минут проверить баню",
        event_type=EventType.DIRECT_MENTION,
    )
    result = service.maybe_schedule(item, now=now)
    assert result.status == "scheduled"
    assert result.recurring is False
    assert result.due_at == now + timedelta(minutes=15)


def test_creator_can_manually_stop_their_group_reminders():
    repo = FakeReminderRepo()
    service = GroupReminderService(repo)
    now = datetime(2026, 9, 14, 15, 10, tzinfo=timezone.utc)
    item = make_event(
        text="НеНой, останови напоминания",
        event_type=EventType.DIRECT_MENTION,
        actor="101",
    )
    result = service.maybe_schedule(item, now=now)
    assert result.status == "cancelled"
    assert result.cancelled_count == 1
    assert repo.manual_cancel_calls == [{
        "scope_id": "-1001",
        "creator_user_id": "101",
        "target_username": None,
    }]


def test_creator_can_stop_only_one_targets_reminders():
    repo = FakeReminderRepo()
    service = GroupReminderService(repo)
    now = datetime(2026, 9, 14, 15, 10, tzinfo=timezone.utc)
    item = make_event(
        text="НеНой, хватит напоминать @toroikin",
        event_type=EventType.DIRECT_MENTION,
        actor="101",
    )
    result = service.maybe_schedule(item, now=now)
    assert result.status == "cancelled"
    assert result.target_username == "toroikin"
    assert repo.manual_cancel_calls[0]["target_username"] == "toroikin"
