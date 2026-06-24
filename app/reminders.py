from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


TIME_RE = re.compile(r"\b(?:в|до)?\s*(?P<hour>[01]?\d|2[0-3])[:.](?P<minute>[0-5]\d)\b")
RELATIVE_RE = re.compile(
    r"\bчерез\s+(?P<amount>\d{1,3})\s*(?P<unit>минут[а-я]*|час[а-я]*)\b",
    re.IGNORECASE,
)
DATE_RE = re.compile(
    r"\b(?P<day>[0-3]?\d)[./](?P<month>[01]?\d)(?:[./](?P<year>\d{2,4}))?\b"
)


@dataclass(frozen=True)
class ReminderDraft:
    task_text: str
    due_at: datetime
    human_due: str


def get_timezone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Europe/Moscow")


def parse_reminder(
    text: str,
    *,
    now: datetime | None = None,
    timezone_name: str = "Europe/Moscow",
) -> ReminderDraft | None:
    cleaned = text.strip()
    if not cleaned:
        return None

    timezone = get_timezone(timezone_name)
    current = now or datetime.now(timezone)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone)

    due_at = parse_relative_due_at(cleaned, current)
    if due_at is None:
        due_at = parse_calendar_due_at(cleaned, current)

    if due_at is None or due_at <= current:
        return None

    task_text = cleanup_task_text(cleaned)
    return ReminderDraft(task_text=task_text, due_at=due_at, human_due=format_due_at(due_at, current))


def parse_relative_due_at(text: str, current: datetime) -> datetime | None:
    match = RELATIVE_RE.search(text.lower())
    if not match:
        return None

    amount = int(match.group("amount"))
    unit = match.group("unit")
    if unit.startswith("час"):
        return current + timedelta(hours=amount)
    return current + timedelta(minutes=amount)


def parse_calendar_due_at(text: str, current: datetime) -> datetime | None:
    lowered = text.lower()
    time_match = TIME_RE.search(lowered)
    if not time_match:
        return None

    hour = int(time_match.group("hour"))
    minute = int(time_match.group("minute"))
    date_match = DATE_RE.search(lowered)

    if date_match:
        day = int(date_match.group("day"))
        month = int(date_match.group("month"))
        year_text = date_match.group("year")
        year = current.year if year_text is None else int(year_text)
        if year < 100:
            year += 2000
        try:
            candidate = current.replace(year=year, month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)
        except ValueError:
            return None
        if candidate <= current and year_text is None:
            candidate = candidate.replace(year=candidate.year + 1)
        return candidate

    if "завтра" in lowered:
        day_offset = 1
    elif "сегодня" in lowered:
        day_offset = 0
    else:
        day_offset = 0

    candidate = (current + timedelta(days=day_offset)).replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )
    if candidate <= current and day_offset == 0 and "сегодня" not in lowered:
        candidate += timedelta(days=1)
    return candidate


def cleanup_task_text(text: str) -> str:
    task = re.sub(r"^/goal\s+", "", text, flags=re.IGNORECASE).strip()
    task = RELATIVE_RE.sub("", task)
    task = DATE_RE.sub("", task)
    task = re.sub(r"\b(сегодня|завтра)\b", "", task, flags=re.IGNORECASE)
    task = TIME_RE.sub("", task)
    task = re.sub(r"\b(в|до)\s*$", "", task, flags=re.IGNORECASE)
    task = re.sub(r"\s+", " ", task).strip(" .,:;—-")
    return task or text.strip()


def format_due_at(due_at: datetime, current: datetime) -> str:
    due_date = due_at.date()
    if due_date == current.date():
        return f"сегодня в {due_at:%H:%M}"
    if due_date == (current + timedelta(days=1)).date():
        return f"завтра в {due_at:%H:%M}"
    return f"{due_at:%d.%m.%Y в %H:%M}"


def build_reminder_message(task_text: str) -> str:
    return (
        f"Время пришло. {task_text} сделал или задача опять живёт только в планах? "
        "Что по результату?"
    )
