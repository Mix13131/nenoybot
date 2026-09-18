from datetime import datetime, timezone

from app_v2.domain.enums import EventType
from app_v2.services.calendar_schedule import CalendarSchedule, next_calendar_occurrence, resolve_local
from app_v2.services.group_reminders import GroupReminderService
from tests_v2.unit.test_group_reminders import FakeReminderRepo, make_event


def test_daily_moscow_and_morning_are_equivalent():
    now = datetime(2026, 1, 2, 5, 0, tzinfo=timezone.utc)
    for phrase in ("НеНой, напоминай каждый день в 9:00 Europe/Moscow", "НеНой, напоминай каждое утро в 9 мск"):
        repo = FakeReminderRepo()
        action = GroupReminderService(repo).maybe_schedule(make_event(text=phrase, event_type=EventType.DIRECT_MENTION), now=now)
        assert action.status == "scheduled"
        assert action.due_at == datetime(2026, 1, 2, 6, 0, tzinfo=timezone.utc)
        assert CalendarSchedule.decode(action.recurrence_rule).frequency == "daily"


def test_weekdays_and_friday():
    service = GroupReminderService(FakeReminderRepo())
    assert service._calendar_spec("напомни по будням в 08:30", datetime.now(timezone.utc))["frequency"] == "weekdays"
    friday = service._calendar_spec("напоминай каждую пятницу в 18:00", datetime.now(timezone.utc))
    assert (friday["frequency"], friday["weekday"]) == ("weekly", 4)


def test_timezone_clarification_continues_original_event():
    repo = FakeReminderRepo(); service = GroupReminderService(repo)
    now = datetime(2026, 1, 2, 5, tzinfo=timezone.utc)
    first = service.maybe_schedule(make_event(text="НеНой, напоминай каждый день в 9:00", event_type=EventType.DIRECT_MENTION), now=now)
    assert first.reason == "timezone_required" and not repo.created
    second = service.maybe_schedule(make_event(text="по московскому времени", event_type=EventType.REPLY_TO_BOT), now=now)
    assert second.status == "scheduled"
    assert repo.created[0]["source_event_id"] == "tg:1"


def test_today_tomorrow_and_explicit_timezone():
    service = GroupReminderService(FakeReminderRepo())
    today = service._calendar_spec("напомни сегодня в 18:00", datetime.now(timezone.utc))
    tomorrow = service._calendar_spec("напомни завтра в 09:00", datetime.now(timezone.utc))
    assert today["one_shot_day"] == 0 and tomorrow["one_shot_day"] == 1
    assert service._timezone("в 9 Europe/Berlin") == "Europe/Berlin"


def test_dst_gap_advances_and_fold_uses_first_occurrence():
    gap = resolve_local(datetime(2026, 3, 29).date(), 2, 30, "Europe/Berlin")
    assert (gap.hour, gap.minute) == (3, 0)
    fold = resolve_local(datetime(2026, 10, 25).date(), 2, 30, "Europe/Berlin")
    assert fold.fold == 0
    schedule = CalendarSchedule("daily", 9, 0, "Europe/Berlin")
    before = datetime(2026, 3, 28, 8, 30, tzinfo=timezone.utc)
    assert next_calendar_occurrence(schedule, before) == datetime(2026, 3, 29, 7, 0, tzinfo=timezone.utc)
