from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


CATALOG_PATH = Path(__file__).with_name("lightness_action_v4.json")
DEFAULT_TIMES = ("09:00", "13:00", "17:00")
GRACE = timedelta(minutes=30)


@dataclass(frozen=True)
class SupportSettings:
    chat_id: int
    mode: str = "coach"
    enabled: bool = False
    timezone: str | None = None
    times: tuple[str, ...] = DEFAULT_TIMES
    paused_until: date | None = None


@dataclass(frozen=True)
class SupportSlot:
    chat_id: int
    local_date: date
    slot_id: str
    due_at: datetime
    content_id: str
    text: str


def load_catalog() -> tuple[dict[str, str], ...]:
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if data.get("version") != "lightness_action_v4":
        raise ValueError("Unsupported support catalog")
    return tuple(data["messages"])


def parse_schedule(value: str) -> tuple[tuple[str, ...], str]:
    """Parse `09:00,13:00,17:00 Europe/Moscow` without changing stored values on error."""
    parts = value.strip().split()
    if len(parts) != 2:
        raise ValueError("Нужно указать часы и IANA timezone, например: 09:00,13:00,17:00 Europe/Moscow")
    raw_times, timezone_name = parts
    values = tuple(dict.fromkeys(item.strip() for item in raw_times.split(",") if item.strip()))
    if not 1 <= len(values) <= 3:
        raise ValueError("Укажи от одного до трёх уникальных времён.")
    for value_time in values:
        try:
            parsed = time.fromisoformat(value_time)
        except ValueError as exc:
            raise ValueError("Время нужно указать как HH:MM.") from exc
        if parsed.second or parsed.microsecond or len(value_time) != 5:
            raise ValueError("Время нужно указать как HH:MM.")
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Неизвестный IANA timezone.") from exc
    return tuple(sorted(values)), timezone_name


def local_slot(local_day: date, hhmm: str, timezone_name: str) -> datetime | None:
    zone = ZoneInfo(timezone_name)
    hour, minute = map(int, hhmm.split(":"))
    local = datetime.combine(local_day, time(hour, minute), tzinfo=zone)
    # A round-trip detects spring-forward wall times that never existed.
    if local.astimezone(UTC).astimezone(zone).replace(fold=0) != local.replace(fold=0):
        return None
    return local.astimezone(UTC)


def next_run(settings: SupportSettings, now: datetime) -> datetime | None:
    if not settings.enabled or not settings.timezone:
        return None
    now = now.astimezone(UTC)
    local_today = now.astimezone(ZoneInfo(settings.timezone)).date()
    for offset in range(0, 370):
        day = local_today + timedelta(days=offset)
        if settings.paused_until and day < settings.paused_until:
            continue
        for hhmm in settings.times:
            candidate = local_slot(day, hhmm, settings.timezone)
            if candidate is not None and candidate > now:
                return candidate
    return None


def due_slots(settings: SupportSettings, now: datetime) -> list[tuple[date, str, datetime]]:
    # Scheduled Lightness messages are an independent subscription.
    # Switching the live chat persona between Trainer and Lightness must not stop them.
    if not settings.enabled or not settings.timezone:
        return []
    now = now.astimezone(UTC)
    local_day = now.astimezone(ZoneInfo(settings.timezone)).date()
    if settings.paused_until and local_day < settings.paused_until:
        return []
    result = []
    for hhmm in settings.times:
        due = local_slot(local_day, hhmm, settings.timezone)
        if due is not None and due <= now <= due + GRACE:
            result.append((local_day, f"time-{hhmm}", due))
    return result


def choose_content(recent_ids: set[str], seed: str = "") -> dict[str, str]:
    catalog = load_catalog()
    available = [item for item in catalog if item["id"] not in recent_ids] or list(catalog)
    return available[sum(ord(char) for char in seed) % len(available)]
