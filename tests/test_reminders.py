from datetime import datetime
from zoneinfo import ZoneInfo

from app.reminders import build_reminder_message, parse_reminder


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


def test_parse_without_time_returns_none() -> None:
    assert parse_reminder("Когда-нибудь сделаю") is None


def test_build_reminder_message_is_not_corporate() -> None:
    message = build_reminder_message("проверить API")

    assert "Время пришло" in message
    assert "Что по результату" in message
