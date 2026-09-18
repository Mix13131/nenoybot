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


def test_complete_valid_iana_timezone_identifiers():
    service = GroupReminderService(FakeReminderRepo())
    assert service._timezone("в 9 America/Argentina/Buenos_Aires") == "America/Argentina/Buenos_Aires"
    assert service._timezone("в 9 America/Indiana/Indianapolis") == "America/Indiana/Indianapolis"
    assert service._timezone("в 9 UTC") == "UTC"
    assert service._timezone("в 9 Invalid/Nowhere") is None
    assert service._timezone("каждый день в 9 присылай отчёт Factory") is None


def test_slashless_tzdb_word_requires_timezone_clarification():
    repo = FakeReminderRepo()
    service = GroupReminderService(repo)
    now = datetime(2026, 1, 2, 5, 0, tzinfo=timezone.utc)
    action = service.maybe_schedule(
        make_event(
            text="НеНой, каждый день в 9 присылай отчёт Factory",
            event_type=EventType.DIRECT_MENTION,
        ),
        now=now,
    )

    assert action.status == "not_scheduled"
    assert action.reason == "timezone_required"
    assert repo.created == []
    assert repo.pending is not None


def test_dst_gap_advances_and_fold_uses_first_occurrence():
    gap = resolve_local(datetime(2026, 3, 29).date(), 2, 30, "Europe/Berlin")
    assert (gap.hour, gap.minute) == (3, 0)
    fold = resolve_local(datetime(2026, 10, 25).date(), 2, 30, "Europe/Berlin")
    assert fold.fold == 0
    schedule = CalendarSchedule("daily", 9, 0, "Europe/Berlin")
    before = datetime(2026, 3, 28, 8, 30, tzinfo=timezone.utc)
    assert next_calendar_occurrence(schedule, before) == datetime(2026, 3, 29, 7, 0, tzinfo=timezone.utc)


def test_pending_timezone_requires_clean_reply_not_direct_mention_or_prose():
    repo = FakeReminderRepo()
    service = GroupReminderService(repo)
    now = datetime(2026, 1, 2, 5, 0, tzinfo=timezone.utc)

    first = service.maybe_schedule(
        make_event(
            text="НеНой, напоминай каждый день в 9:00",
            event_type=EventType.DIRECT_MENTION,
            metadata={"message_thread_id": 777},
        ),
        now=now,
    )
    assert first.reason == "timezone_required"
    assert repo.pending is not None

    unrelated_direct = service.maybe_schedule(
        make_event(
            text="Europe/Berlin",
            event_type=EventType.DIRECT_MENTION,
            metadata={"message_thread_id": 777},
        ),
        now=now,
    )
    assert unrelated_direct is None
    assert repo.pending is not None
    assert repo.created == []

    unrelated_reply = service.maybe_schedule(
        make_event(
            text="расскажи про Europe/Berlin",
            event_type=EventType.REPLY_TO_BOT,
            metadata={"message_thread_id": 777},
        ),
        now=now,
    )
    assert unrelated_reply is None
    assert repo.pending is not None
    assert repo.created == []


def test_clean_timezone_reply_completes_same_thread_and_preserves_original_provenance():
    repo = FakeReminderRepo()
    service = GroupReminderService(repo)
    now = datetime(2026, 1, 2, 5, 0, tzinfo=timezone.utc)

    service.maybe_schedule(
        make_event(
            text="НеНой, напоминай каждый день в 9:00",
            event_type=EventType.DIRECT_MENTION,
            metadata={"message_thread_id": 777},
        ),
        now=now,
    )
    action = service.maybe_schedule(
        make_event(
            text="по московскому времени",
            event_type=EventType.REPLY_TO_BOT,
            metadata={"message_thread_id": 777},
        ),
        now=now,
    )

    assert action.status == "scheduled"
    assert repo.pending is None
    assert len(repo.created) == 1
    assert repo.created[0]["source_event_id"] == "tg:1"
    assert repo.created[0]["payload"]["source_message_id"] == "55"
    assert repo.created[0]["payload"]["message_thread_id"] == 777


def test_pending_timezone_reply_from_another_topic_does_not_complete_intent():
    repo = FakeReminderRepo()
    service = GroupReminderService(repo)
    now = datetime(2026, 1, 2, 5, 0, tzinfo=timezone.utc)

    service.maybe_schedule(
        make_event(
            text="НеНой, напоминай каждый день в 9:00",
            event_type=EventType.DIRECT_MENTION,
            metadata={"message_thread_id": 100},
        ),
        now=now,
    )
    action = service.maybe_schedule(
        make_event(
            text="Europe/Berlin",
            event_type=EventType.REPLY_TO_BOT,
            metadata={"message_thread_id": 200},
        ),
        now=now,
    )

    assert action is None
    assert repo.pending is not None
    assert repo.created == []


def test_direct_calendar_reminder_preserves_forum_topic():
    repo = FakeReminderRepo()
    service = GroupReminderService(repo)
    now = datetime(2026, 1, 2, 5, 0, tzinfo=timezone.utc)

    action = service.maybe_schedule(
        make_event(
            text="НеНой, напоминай каждый день в 9:00 Europe/Moscow",
            event_type=EventType.DIRECT_MENTION,
            metadata={"message_thread_id": 321},
        ),
        now=now,
    )

    assert action.status == "scheduled"
    assert repo.created[0]["payload"]["message_thread_id"] == 321


def test_moscow_alias_has_boundaries_and_multiple_timezones_fail_closed():
    service = GroupReminderService(FakeReminderRepo())

    assert service._timezone("каждый день в 9 присылай погоду в Омске") is None
    assert service._timezone("каждый день в 9 мск") == "Europe/Moscow"
    assert service._timezone("Europe/Moscow по московскому времени") == "Europe/Moscow"
    assert service._timezone("Europe/Berlin America/New_York") is None
    assert service._timezone("Europe/Berlin Invalid/Nowhere") is None


def test_unsupported_or_contradictory_day_periods_fail_closed():
    repo = FakeReminderRepo()
    service = GroupReminderService(repo)
    now = datetime(2026, 1, 2, 5, 0, tzinfo=timezone.utc)

    for text in (
        "НеНой, напоминай каждый день в 9 вечера Europe/Moscow",
        "НеНой, напоминай каждый день в 9 дня Europe/Moscow",
        "НеНой, напоминай каждый день в 18 утра Europe/Moscow",
        "НеНой, напоминай каждое утро в 18 Europe/Moscow",
    ):
        action = service.maybe_schedule(
            make_event(text=text, event_type=EventType.DIRECT_MENTION),
            now=now,
        )
        assert action.status == "not_scheduled"
        assert repo.created == []

    valid = service.maybe_schedule(
        make_event(
            text="НеНой, напоминай каждый день в 9 утра Europe/Moscow",
            event_type=EventType.DIRECT_MENTION,
        ),
        now=now,
    )
    assert valid.status == "scheduled"
