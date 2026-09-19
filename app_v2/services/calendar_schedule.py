from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class CalendarSchedule:
    frequency: str
    hour: int
    minute: int
    timezone: str
    weekday: int | None = None

    def __post_init__(self) -> None:
        if self.frequency not in {"daily", "weekdays", "weekly"}:
            raise ValueError("Unsupported calendar frequency")
        if not 0 <= self.hour <= 23 or not 0 <= self.minute <= 59:
            raise ValueError("Invalid local reminder time")
        if self.frequency == "weekly" and self.weekday not in range(7):
            raise ValueError("Weekly schedule requires weekday")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown IANA timezone") from exc

    def encode(self) -> str:
        body = {"frequency": self.frequency, "hour": self.hour, "minute": self.minute,
                "timezone": self.timezone}
        if self.weekday is not None:
            body["weekday"] = self.weekday
        return "calendar:" + json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @classmethod
    def decode(cls, value: str) -> "CalendarSchedule":
        if not value.startswith("calendar:"):
            raise ValueError("Not a calendar recurrence_rule")
        try:
            body = json.loads(value[len("calendar:"):])
            return cls(body["frequency"], int(body["hour"]), int(body["minute"]),
                       body["timezone"], body.get("weekday"))
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid calendar recurrence_rule") from exc


def validate_timezone(name: str) -> str:
    try:
        zone = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Unknown IANA timezone") from exc
    return zone.key


def resolve_local(local_date: date, hour: int, minute: int, timezone_name: str) -> datetime:
    """First fold wins; a DST gap advances minute-by-minute to valid wall time."""
    zone = ZoneInfo(validate_timezone(timezone_name))
    naive = datetime.combine(local_date, time(hour, minute))
    for offset in range(24 * 60):
        candidate_naive = naive + timedelta(minutes=offset)
        candidate = candidate_naive.replace(tzinfo=zone, fold=0)
        roundtrip = candidate.astimezone(timezone.utc).astimezone(zone)
        if roundtrip.replace(tzinfo=None) == candidate_naive and roundtrip.fold == 0:
            return candidate
    raise ValueError("No valid local time within one day")


def next_calendar_occurrence(schedule: CalendarSchedule, after: datetime) -> datetime:
    if after.tzinfo is None or after.utcoffset() is None:
        raise ValueError("after must be timezone-aware")
    local_after = after.astimezone(ZoneInfo(schedule.timezone))
    for days in range(0, 370):
        day = local_after.date() + timedelta(days=days)
        if schedule.frequency == "weekdays" and day.weekday() >= 5:
            continue
        if schedule.frequency == "weekly" and day.weekday() != schedule.weekday:
            continue
        candidate = resolve_local(day, schedule.hour, schedule.minute, schedule.timezone)
        if candidate.astimezone(timezone.utc) > after.astimezone(timezone.utc):
            return candidate.astimezone(timezone.utc)
    raise ValueError("Could not find next calendar occurrence")
