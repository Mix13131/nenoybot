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


def test_pending_one_shot_preserves_original_calendar_day_across_midnight():
    for word, expected_due in (
        ("сегодня", datetime(2026, 1, 2, 18, 0, tzinfo=timezone.utc)),
        ("завтра", datetime(2026, 1, 3, 9, 0, tzinfo=timezone.utc)),
    ):
        repo = FakeReminderRepo()
        service = GroupReminderService(repo)
        original = datetime(2026, 1, 2, 23, 50, tzinfo=timezone.utc)
        original_event = make_event(
            text=f"НеНой, напомни {word} в {expected_due:%H:%M}",
            event_type=EventType.DIRECT_MENTION,
        ).model_copy(update={"occurred_at": original})
        service.maybe_schedule(original_event, now=original)

        clarification_time = datetime(2026, 1, 3, 8, 0, tzinfo=timezone.utc)
        reply_event = make_event(
            text="UTC",
            event_type=EventType.REPLY_TO_BOT,
        ).model_copy(update={"occurred_at": clarification_time})
        action = service.maybe_schedule(reply_event, now=clarification_time)

        if word == "сегодня":
            assert action.status == "not_scheduled"
            assert action.reason == "calendar_time_in_past"
            assert repo.created == []
            assert repo.pending is not None
        else:
            assert action.status == "scheduled"
            assert action.due_at == expected_due


def test_direct_one_shot_with_timezone_is_unchanged():
    repo = FakeReminderRepo()
    request_time = datetime(2026, 1, 2, 23, 50, tzinfo=timezone.utc)
    event = make_event(
        text="НеНой, напомни завтра в 09:00 UTC",
        event_type=EventType.DIRECT_MENTION,
    ).model_copy(update={"occurred_at": request_time})
    action = GroupReminderService(repo).maybe_schedule(
        event,
        now=request_time,
    )
    assert action.status == "scheduled"
    assert action.due_at == datetime(2026, 1, 3, 9, 0, tzinfo=timezone.utc)


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


def test_malformed_time_tails_fail_closed_while_valid_forms_remain_supported():
    now = datetime(2026, 1, 2, 5, 0, tzinfo=timezone.utc)
    for time_expression in ("9:99", "9:3", "09:0", "9 вечером"):
        repo = FakeReminderRepo()
        action = GroupReminderService(repo).maybe_schedule(
            make_event(
                text=f"НеНой, напоминай каждый день в {time_expression} Europe/Moscow",
                event_type=EventType.DIRECT_MENTION,
            ),
            now=now,
        )
        assert action.status == "not_scheduled"
        assert action.reason == "unsupported_time_expression"
        assert repo.created == []

    for phrase in (
        "НеНой, напоминай каждый день в 9 Europe/Moscow",
        "НеНой, напоминай каждый день в 9:00 Europe/Moscow",
        "НеНой, напоминай каждый день в 09:00 Europe/Moscow",
        "НеНой, напоминай каждый день в 9 утра Europe/Moscow",
        "НеНой, напоминай каждое утро в 9 Europe/Moscow",
    ):
        repo = FakeReminderRepo()
        assert GroupReminderService(repo).maybe_schedule(
            make_event(text=phrase, event_type=EventType.DIRECT_MENTION), now=now,
        ).status == "scheduled"


def test_one_shot_reference_uses_event_time_not_delayed_worker_time():
    repo = FakeReminderRepo()
    service = GroupReminderService(repo)
    event_time = datetime(2026, 1, 2, 23, 50, tzinfo=timezone.utc)
    processing_time = datetime(2026, 1, 3, 0, 10, tzinfo=timezone.utc)
    original = make_event(
        text="НеНой, напомни завтра в 09:00",
        event_type=EventType.DIRECT_MENTION,
    ).model_copy(update={"occurred_at": event_time})

    first = service.maybe_schedule(original, now=processing_time)
    assert first.status == "not_scheduled"
    assert first.reason == "timezone_required"
    assert repo.pending["payload"]["reference_at"] == event_time.isoformat()

    clarification_time = datetime(2026, 1, 3, 8, 0, tzinfo=timezone.utc)
    reply = make_event(
        text="UTC",
        event_type=EventType.REPLY_TO_BOT,
    ).model_copy(update={"occurred_at": clarification_time})
    completed = service.maybe_schedule(reply, now=clarification_time)

    assert completed.status == "scheduled"
    assert completed.due_at == datetime(2026, 1, 3, 9, 0, tzinfo=timezone.utc)


def test_calendar_target_and_stop_on_reply_match_interval_metadata():
    repo = FakeReminderRepo()
    service = GroupReminderService(repo)
    now = datetime(2026, 1, 2, 5, 0, tzinfo=timezone.utc)

    action = service.maybe_schedule(
        make_event(
            text="НеНой, напоминай @alice каждый день в 19:00 Europe/Moscow, пока она не ответит",
            event_type=EventType.DIRECT_MENTION,
        ),
        now=now,
    )

    assert action.status == "scheduled"
    assert action.target_username == "alice"
    assert action.stop_on_reply is True
    payload = repo.created[0]["payload"]
    assert payload["target_username"] == "alice"
    assert payload["stop_on_reply"] is True
    assert "@alice" in payload["text"]


def test_calendar_target_and_stop_survive_timezone_clarification():
    repo = FakeReminderRepo()
    service = GroupReminderService(repo)
    now = datetime(2026, 1, 2, 5, 0, tzinfo=timezone.utc)

    first = service.maybe_schedule(
        make_event(
            text="НеНой, напоминай @alice каждый день в 19:00, пока она не ответит",
            event_type=EventType.DIRECT_MENTION,
        ),
        now=now,
    )
    assert first.reason == "timezone_required"
    assert repo.pending["payload"]["target_username"] == "alice"
    assert repo.pending["payload"]["stop_on_reply"] is True

    reply = make_event(
        text="Europe/Moscow",
        event_type=EventType.REPLY_TO_BOT,
    ).model_copy(update={"occurred_at": now})
    completed = service.maybe_schedule(reply, now=now)

    assert completed.status == "scheduled"
    assert completed.target_username == "alice"
    assert completed.stop_on_reply is True
    assert repo.created[0]["payload"]["target_username"] == "alice"
    assert repo.created[0]["payload"]["stop_on_reply"] is True


def test_multiple_calendar_times_fail_closed():
    repo = FakeReminderRepo()
    service = GroupReminderService(repo)
    now = datetime(2026, 1, 2, 5, 0, tzinfo=timezone.utc)

    action = service.maybe_schedule(
        make_event(
            text="НеНой, напоминай каждый день в 9:00 или в 10:00 Europe/Moscow",
            event_type=EventType.DIRECT_MENTION,
        ),
        now=now,
    )

    assert action.status == "not_scheduled"
    assert action.reason == "unsupported_time_expression"
    assert repo.created == []



def test_clock_alternative_without_repeated_preposition_is_ambiguous_but_year_prose_is_not():
    now = datetime(2026, 1, 2, 5, 0, tzinfo=timezone.utc)

    ambiguous_repo = FakeReminderRepo()
    ambiguous = GroupReminderService(ambiguous_repo).maybe_schedule(
        make_event(
            text="НеНой, напоминай каждый день в 9:00 или 10:00 Europe/Moscow",
            event_type=EventType.DIRECT_MENTION,
        ),
        now=now,
    )
    assert ambiguous.status == "not_scheduled"
    assert ambiguous.reason == "unsupported_time_expression"
    assert ambiguous_repo.created == []

    valid_repo = FakeReminderRepo()
    valid = GroupReminderService(valid_repo).maybe_schedule(
        make_event(
            text="НеНой, напоминай каждый день в 9:00, что дедлайн в 2026 году Europe/Moscow",
            event_type=EventType.DIRECT_MENTION,
        ),
        now=now,
    )
    assert valid.status == "scheduled"
    assert valid_repo.created
    assert CalendarSchedule.decode(valid.recurrence_rule).hour == 9


def test_calendar_recurring_payload_is_until_cancelled_not_four_occurrences():
    repo = FakeReminderRepo()
    action = GroupReminderService(repo).maybe_schedule(
        make_event(
            text="НеНой, напоминай каждый день в 9:00 Europe/Moscow",
            event_type=EventType.DIRECT_MENTION,
        ),
        now=datetime(2026, 1, 2, 5, 0, tzinfo=timezone.utc),
    )

    assert action.status == "scheduled"
    payload = repo.created[0]["payload"]
    assert payload["recurrence_policy"] == "until_cancelled"
    assert "max_occurrences" not in payload



def test_calendar_words_in_reminder_subject_do_not_create_false_ambiguity():
    now = datetime(2026, 1, 2, 5, 0, tzinfo=timezone.utc)
    for text, expected_frequency in (
        ("НеНой, напоминай каждый день в 9:00 присылать прогноз на завтра Europe/Moscow", "daily"),
        ("НеНой, напоминай каждую пятницу в 18:00, что завтра выходной Europe/Moscow", "weekly"),
        ("НеНой, напоминай каждый день в 9:00 спросить, выбрать сегодня или завтра Europe/Moscow", "daily"),
        ("НеНой, напоминай каждый день в 9:00 сказать, что завтра лучше отдохнуть Europe/Moscow", "daily"),
        ("НеНой, напоминай про прогноз на завтра каждый день в 9:00 Europe/Moscow", "daily"),
        ("НеНой, напоминай каждый день сверять задачи на сегодня в 9:00 Europe/Moscow", "daily"),
        ("НеНой, напоминай каждый день смотреть прогноз на завтра в 9:00 Europe/Moscow", "daily"),
        ("НеНой, напоминай каждый день в 9:00, что лучше сделать сегодня Europe/Moscow", "daily"),
    ):
        repo = FakeReminderRepo()
        action = GroupReminderService(repo).maybe_schedule(
            make_event(text=text, event_type=EventType.DIRECT_MENTION),
            now=now,
        )
        assert action.status == "scheduled"
        assert repo.created
        assert action.recurring
        assert CalendarSchedule.decode(action.recurrence_rule).frequency == expected_frequency


def test_competing_calendar_kinds_fail_closed():
    now = datetime(2026, 1, 2, 5, 0, tzinfo=timezone.utc)
    for text in (
        "НеНой, напоминай каждый день или каждую пятницу в 9:00 Europe/Moscow",
        "НеНой, напомни сегодня или завтра в 18:00 Europe/Moscow",
        "НеНой, напоминай каждый день в 9:00 или каждую пятницу Europe/Moscow",
        "НеНой, напомни сегодня в 18:00 или завтра Europe/Moscow",
        "НеНой, напоминай каждый день или лучше каждую пятницу в 9:00 Europe/Moscow",
        "НеНой, напомни сегодня в 18:00 UTC, или лучше завтра",
        "НеНой, напоминай каждый день, а лучше по будням в 9:00 Europe/Moscow",
        "НеНой, напоминай каждый день, точнее по будням в 9:00 Europe/Moscow",
        "НеНой, напоминай каждый день или всё-таки каждую пятницу в 9:00 Europe/Moscow",
        "НеНой, напоминай не каждый день, а каждую пятницу в 9:00 Europe/Moscow",
        "НеНой, напоминай каждый день или же каждую пятницу в 9:00 Europe/Moscow",
        "НеНой, напоминай каждый день, но лучше по будням в 9:00 Europe/Moscow",
        "НеНой, напоминай каждый день, но вообще-то лучше по будням в 9:00 Europe/Moscow",
        "НеНой, напоминай каждый день, а лучше на завтра в 9:00 Europe/Moscow",
        "НеНой, напоминай каждый день или, пожалуй, каждую пятницу в 9:00 Europe/Moscow",
        "НеНой, напоминай каждый день или, может быть, каждую пятницу в 9:00 Europe/Moscow",
    ):
        repo = FakeReminderRepo()
        action = GroupReminderService(repo).maybe_schedule(
            make_event(text=text, event_type=EventType.DIRECT_MENTION),
            now=now,
        )
        assert action.status == "not_scheduled"
        assert action.reason == "unsupported_time_expression"
        assert repo.created == []
