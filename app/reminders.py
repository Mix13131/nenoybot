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
REPORT_KEYWORDS = (
    "отчет",
    "отчёт",
    "отчета",
    "отчетов",
    "отчёта",
    "отчёты",
    "отчётов",
    "результат",
)
REMINDER_KEYWORDS = (
    "напоминание",
    "напомни",
    "чек-ин",
    "чек-ином",
    "чек",
    "напомин",
)
TIME_CONTEXT_WINDOW = 24
TASK_KEYWORDS = (
    "срок",
    "дедлайн",
    "задач",
    "цель",
)
UNAVAILABLE_KEYWORDS = (
    "wind down",
    "winddown",
    "режим тишины",
    "не смогу",
    "не могу писать",
    "не доступен",
    "не доступна",
    "недоступен",
    "недоступна",
    "в монастырь",
    "уходит в",
)


def _contains_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in keywords)


def _with_timezone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Europe/Moscow")


@dataclass(frozen=True)
class ReminderDraft:
    task_text: str
    due_at: datetime
    human_due: str


@dataclass(frozen=True)
class Commitment:
    task_text: str
    task_deadline: datetime | None
    report_time: datetime | None
    reminder_time: datetime | None
    unavailable_after: datetime | None

    @property
    def active_checkin_time(self) -> datetime | None:
        return self.report_time or self.task_deadline

    def has_any_time(self) -> bool:
        return any(
            value is not None
            for value in (
                self.task_deadline,
                self.report_time,
                self.reminder_time,
                self.unavailable_after,
            )
        )


def parse_commitment(
    text: str,
    *,
    now: datetime | None = None,
    timezone_name: str = "Europe/Moscow",
) -> Commitment | None:
    cleaned = text.strip()
    if not cleaned:
        return None

    timezone = _with_timezone(timezone_name)
    current = now or datetime.now(timezone)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone)

    typed_times = _extract_typed_times(cleaned, current)

    task_deadline = typed_times.get("task")
    report_time = typed_times.get("report")
    reminder_time = typed_times.get("reminder")
    unavailable_after = typed_times.get("unavailable")

    if not task_deadline and not report_time and not reminder_time and not unavailable_after:
        due_at = parse_relative_due_at(cleaned, current)
        if due_at is None:
            due_at = parse_calendar_due_at(cleaned, current)
        if due_at is None or due_at <= current:
            return None
        task_deadline = due_at

    task_text = cleanup_task_text(cleaned)
    return Commitment(
        task_text=task_text,
        task_deadline=task_deadline,
        report_time=report_time,
        reminder_time=reminder_time,
        unavailable_after=unavailable_after,
    )


def parse_reminder(
    text: str,
    *,
    now: datetime | None = None,
    timezone_name: str = "Europe/Moscow",
) -> ReminderDraft | None:
    commitment = parse_commitment(text, now=now, timezone_name=timezone_name)
    if commitment is None:
        return None

    timezone = _with_timezone(timezone_name)
    current = (now or datetime.now(timezone)).astimezone(timezone)

    due_at = commitment.active_checkin_time
    if due_at is None or due_at <= current:
        return None

    return ReminderDraft(
        task_text=commitment.task_text,
        due_at=due_at,
        human_due=format_due_at(due_at, current),
    )


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


def _extract_typed_times(text: str, current: datetime) -> dict[str, datetime]:
    lowered = text.lower()
    found: dict[str, tuple[datetime, int]] = {}

    for time_match in TIME_RE.finditer(lowered):
        segment = lowered[max(0, time_match.start() - 35) : min(len(lowered), time_match.end() + 15)]
        due_at = _parse_time_from_match(time_match, segment, current)
        if due_at is None or due_at <= current:
            continue

        kind = _detect_time_kind(lowered, time_match.start(), time_match.end())
        if kind is None:
            continue

        previous = found.get(kind)
        if previous is None or time_match.start() > previous[1]:
            found[kind] = (due_at, time_match.start())

    return {kind: due for kind, (due, _index) in found.items()}


def _parse_time_from_match(time_match: re.Match[str], segment: str, current: datetime) -> datetime | None:
    try:
        hour = int(time_match.group("hour"))
        minute = int(time_match.group("minute"))
    except (IndexError, ValueError):
        return None

    # Prefer explicit date in the surrounding segment.
    date_match = DATE_RE.search(segment)
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

    if "завтра" in segment:
        day_offset = 1
    else:
        day_offset = 0

    candidate = (current + timedelta(days=day_offset)).replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )
    if candidate <= current and day_offset == 0 and "сегодня" not in segment:
        candidate += timedelta(days=1)
    return candidate


def _find_nearest_keyword(
    text: str,
    start: int,
    keywords: tuple[str, ...],
    *,
    after: bool = False,
) -> tuple[int, int] | None:
    nearest_pos: int | None = None
    nearest_distance: int | None = None

    for keyword in keywords:
        if after:
            pos = text.find(keyword, start)
            if pos == -1:
                continue
            distance = pos - start
        else:
            pos = text.rfind(keyword, 0, start)
            if pos == -1:
                continue
            distance = start - (pos + len(keyword))

        if distance > TIME_CONTEXT_WINDOW:
            continue
        if nearest_distance is None or distance < nearest_distance:
            nearest_distance = distance
            nearest_pos = pos

    if nearest_pos is None:
        return None
    return nearest_pos, nearest_distance


def _contains_keyword_between(text: str, start: int, end: int, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text[start:end] for keyword in keywords)


def _detect_time_kind(full_text: str, start: int, end: int) -> str | None:
    # Prefer explicit keywords nearby the time, both before and after.
    candidates: list[tuple[str, int]] = []

    for kind, keywords in (
        ("report", REPORT_KEYWORDS),
        ("reminder", REMINDER_KEYWORDS),
        ("unavailable", UNAVAILABLE_KEYWORDS),
        ("task", TASK_KEYWORDS),
    ):
        before = _find_nearest_keyword(full_text, start, keywords)
        if before is not None:
            _, distance = before
            candidates.append((kind, distance))

        after = _find_nearest_keyword(full_text, end, keywords, after=True)
        if after is not None:
            _, distance = after
            # If keyword exists right after time, it usually describes this exact slot
            # (например: «21:00 wind down»).
            candidates.append((kind, distance))

    found = [(kind, distance) for kind, distance in candidates]
    if found:
        return sorted(found, key=lambda item: item[1])[0][0]

    if _contains_keyword_between(full_text, end, min(len(full_text), end + 30), REPORT_KEYWORDS):
        return "report"
    if _contains_keyword_between(full_text, end, min(len(full_text), end + 30), REMINDER_KEYWORDS):
        return "reminder"
    if _contains_keyword_between(full_text, end, min(len(full_text), end + 30), UNAVAILABLE_KEYWORDS):
        return "unavailable"
    if _contains_keyword_between(full_text, end, min(len(full_text), end + 30), TASK_KEYWORDS):
        return "task"
    return None


def get_timezone(timezone_name: str) -> ZoneInfo:
    return _with_timezone(timezone_name)


def cleanup_task_text(text: str) -> str:
    task = re.sub(r"^/goal\s+", "", text, flags=re.IGNORECASE).strip()
    task = re.sub(
        r"\b(отчет|отчета|отчёта|результат|напоминание|напомин|напомни|wind down|winddown|режим тишины|не смогу|не могу писать|не доступен|не доступна|недоступен|недоступна)\b",
        "",
        task,
        flags=re.IGNORECASE,
    )
    task = RELATIVE_RE.sub("", task)
    task = DATE_RE.sub("", task)
    task = re.sub(r"\b(сегодня|завтра)\b", "", task, flags=re.IGNORECASE)
    task = TIME_RE.sub("", task)
    task = re.sub(r"\b(в|до)\s*$", "", task, flags=re.IGNORECASE)
    task = re.sub(r"\s+", " ", task).strip(" .,:;—-")
    if not task:
        return text.strip()
    return task


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
