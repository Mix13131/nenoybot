from datetime import datetime
from zoneinfo import ZoneInfo

from app.reminders import build_reminder_message, parse_commitment, parse_reminder


def test_parse_today_reminder() -> None:
    now = datetime(2026, 6, 24, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))

    reminder = parse_reminder("Сегодня в 19:00 проверю API в Make", now=now)

    assert reminder is not None
    assert reminder.due_at.hour == 19
    assert reminder.due_at.minute == 0
    assert reminder.human_due == "сегодня в 19:00"
    assert "проверю API" in reminder.task_text


def test_parse_tomorrow_reminder() -> None:
    now = datetime(2026, 6, 24, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))

    reminder = parse_reminder("Завтра до 12:00 отправлю отчёт", now=now)

    assert reminder is not None
    assert reminder.due_at.day == 25
    assert reminder.human_due == "завтра в 12:00"


def test_parse_relative_minutes_reminder() -> None:
    now = datetime(2026, 6, 24, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))

    reminder = parse_reminder("Через 30 минут вернусь с результатом", now=now)

    assert reminder is not None
    assert reminder.due_at.hour == 10
    assert reminder.due_at.minute == 30


def test_parse_commitment_split_report_reminder_times() -> None:
    now = datetime(2026, 6, 24, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))

    commitment = parse_commitment(
        "Напоминание в 20:40 и отчёт в 20:45, после этого wind down в 21:00",
        now=now,
    )

    assert commitment is not None
    assert commitment.reminder_time is not None
    assert commitment.reminder_time.hour == 20
    assert commitment.reminder_time.minute == 40
    assert commitment.report_time is not None
    assert commitment.report_time.hour == 20
    assert commitment.report_time.minute == 45
    assert commitment.unavailable_after is not None
    assert commitment.unavailable_after.hour == 21
    assert commitment.unavailable_after.minute == 0
    assert commitment.active_checkin_time == commitment.report_time


def test_commitment_active_checkin_prefers_report_before_reminder_and_task() -> None:
    now = datetime(2026, 6, 24, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))

    commitment = parse_commitment(
        "Напоминание в 20:40, отчёт в 20:45, задача до 22:00",
        now=now,
    )

    assert commitment is not None
    assert commitment.reminder_time is not None
    assert commitment.report_time is not None
    assert commitment.task_deadline is not None
    assert commitment.active_checkin_time == commitment.report_time


def test_wind_down_uses_report_time_not_unavailable_time() -> None:
    now = datetime(2026, 6, 24, 10, 0, tzinfo=ZoneInfo("Europe/Moscow"))

    commitment = parse_commitment(
        "напомни в 20:40, отчет в 20:45, после 21:00 wind down",
        now=now,
    )

    assert commitment is not None
    assert commitment.reminder_time is not None
    assert commitment.reminder_time.hour == 20
    assert commitment.reminder_time.minute == 40
    assert commitment.report_time is not None
    assert commitment.report_time.hour == 20
    assert commitment.report_time.minute == 45
    assert commitment.unavailable_after is not None
    assert commitment.unavailable_after.hour == 21
    assert commitment.unavailable_after.minute == 0
    assert commitment.active_checkin_time == commitment.report_time


def test_parse_without_time_returns_none() -> None:
    assert parse_reminder("Когда-нибудь сделаю") is None


def test_build_reminder_message_is_not_corporate() -> None:
    message = build_reminder_message("проверить API")

    assert "Время пришло" in message
    assert "Что по результату" in message
