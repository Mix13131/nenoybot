from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.services.group_reminders import GroupReminderService


class FakeReminderRepo:
    def __init__(self):
        self.created = []
        self.cancel_calls = []
        self.reply_cancel_calls = []
        self.active_cancel_calls = []
        self.pending = None
        self.already_existing = False

    def create(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id=17, due_at=kwargs["due_at"], payload=kwargs["payload"],
                               recurrence_rule=kwargs["recurrence_rule"],
                               already_existing=self.already_existing)

    def save_pending_calendar_intent(self, **kwargs):
        self.pending = {"id": 3, "source_event_id": kwargs["source_event_id"], "payload": kwargs["payload"]}

    def get_pending_calendar_intent(self, **kwargs):
        return self.pending

    def complete_pending_calendar_intent(self, intent_id):
        self.pending = None

    def cancel_waiting_for_response(self, **kwargs):
        self.cancel_calls.append(kwargs)
        return 2

    def cancel_reminder_from_bot_reply(self, **kwargs):
        self.reply_cancel_calls.append(kwargs)
        return 1

    def cancel_active_group_reminders(self, **kwargs):
        self.active_cancel_calls.append(kwargs)
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
    assert repo.created[0]["payload"]["max_occurrences"] == 4
    assert repo.created[0]["payload"]["fire_count"] == 0


def test_too_frequent_recurring_reminder_is_rejected():
    repo = FakeReminderRepo()
    service = GroupReminderService(repo)
    now = datetime(2026, 9, 14, 15, 10, tzinfo=timezone.utc)
    item = make_event(
        text="НеНой, напоминай @toroikin каждые 5 минут",
        event_type=EventType.DIRECT_MENTION,
    )
    result = service.maybe_schedule(item, now=now)
    assert result.status == "not_scheduled"
    assert result.reason == "unsupported_time_expression"
    assert repo.created == []


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
    assert repo.created[0]["payload"]["max_occurrences"] == 1


def test_interval_and_relative_retries_report_honest_no_change():
    now = datetime(2026, 9, 14, 15, 10, tzinfo=timezone.utc)
    for text in (
        "НеНой, напоминай каждые 30 минут проверить баню",
        "НеНой, напомни через 15 минут проверить баню",
    ):
        repo = FakeReminderRepo()
        repo.already_existing = True
        action = GroupReminderService(repo).maybe_schedule(
            make_event(text=text, event_type=EventType.DIRECT_MENTION), now=now
        )
        assert action.status == "already_scheduled"
        assert action.reminder_id == 17
        assert action.due_at == repo.created[0]["due_at"]


def test_reply_stop_cancels_exact_bot_reminder_chain():
    repo = FakeReminderRepo()
    service = GroupReminderService(repo)
    now = datetime(2026, 9, 14, 15, 10, tzinfo=timezone.utc)
    item = make_event(text="стоп", event_type=EventType.REPLY_TO_BOT, actor="999")
    result = service.maybe_schedule(item, now=now)
    assert result.status == "cancelled"
    assert result.cancelled_count == 1
    assert result.reason == "reply_to_reminder"
    assert repo.reply_cancel_calls == [{
        "scope_id": "-1001",
        "reply_to_message_id": "54",
    }]


def test_any_participant_can_stop_all_group_reminders():
    repo = FakeReminderRepo()
    service = GroupReminderService(repo)
    now = datetime(2026, 9, 14, 15, 10, tzinfo=timezone.utc)
    item = make_event(
        text="НеНой, останови все напоминания",
        event_type=EventType.DIRECT_MENTION,
        actor="999",
    )
    result = service.maybe_schedule(item, now=now)
    assert result.status == "cancelled"
    assert result.cancelled_count == 1
    assert repo.active_cancel_calls == [{
        "scope_id": "-1001",
        "target_username": None,
    }]


def test_any_participant_can_stop_one_targets_reminders():
    repo = FakeReminderRepo()
    service = GroupReminderService(repo)
    now = datetime(2026, 9, 14, 15, 10, tzinfo=timezone.utc)
    item = make_event(
        text="НеНой, хватит напоминать @toroikin",
        event_type=EventType.DIRECT_MENTION,
        actor="999",
    )
    result = service.maybe_schedule(item, now=now)
    assert result.status == "cancelled"
    assert result.target_username == "toroikin"
    assert repo.active_cancel_calls[0]["target_username"] == "toroikin"


def test_natural_gorshochek_phrase_stops_target_reminder():
    repo = FakeReminderRepo()
    service = GroupReminderService(repo)
    now = datetime(2026, 9, 14, 21, 9, tzinfo=timezone.utc)
    item = make_event(
        text="Горшочек, не вари. Все, достаточно напоминать @toroikin про баню",
        event_type=EventType.REPLY_TO_BOT,
        actor="101",
    )
    result = service.maybe_schedule(item, now=now)
    assert result.status == "cancelled"
    assert result.target_username == "toroikin"
    assert result.cancelled_count == 1
