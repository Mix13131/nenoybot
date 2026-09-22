from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


LAB_DATASET_VERSION = 1
LAB_REPLAY_VERSION = 1

_URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
_TELEGRAM_LINK_RE = re.compile(r"(?i)\b(?:t\.me|telegram\.me)/\S+")
_EMAIL_RE = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Z]{2,}(?!\w)")
_HANDLE_RE = re.compile(r"(?<!\w)@[A-Za-z0-9_]{5,}")
_PHONE_RE = re.compile(r"(?<!\w)\+?\d(?:[\s().-]*\d){9,}(?!\w)")
_LONG_NUMBER_RE = re.compile(r"(?<!\d)\d{6,}(?!\d)")
_SPACE_RE = re.compile(r"[ \t]+")


@dataclass(frozen=True)
class GroupLabOptions:
    owner_source_id: str | None = None
    owner_display_name: str | None = None
    include_service_messages: bool = True


@dataclass(frozen=True)
class ReplayOptions:
    inactivity_gap_minutes: int = 90
    max_episode_messages: int = 60
    context_messages: int = 12

    def validate(self) -> None:
        if self.inactivity_gap_minutes < 1:
            raise ValueError("inactivity_gap_minutes must be >= 1")
        if self.max_episode_messages < 2:
            raise ValueError("max_episode_messages must be >= 2")
        if self.context_messages < 1:
            raise ValueError("context_messages must be >= 1")


def _flatten_telegram_text(value: Any) -> str:
    """Flatten Telegram Desktop export text while preserving human-readable order."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        text = value.get("text")
        return text if isinstance(text, str) else ""
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "".join(_flatten_telegram_text(item) for item in value)
    return str(value)


def _iso_timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    if dt.tzinfo is None:
        return dt.isoformat(timespec="seconds")
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _source_actor(row: Mapping[str, Any]) -> tuple[str | None, str | None]:
    source_id = row.get("from_id") if row.get("type") == "message" else row.get("actor_id")
    display = row.get("from") if row.get("type") == "message" else row.get("actor")
    source_id_text = str(source_id).strip() if source_id is not None and str(source_id).strip() else None
    display_text = str(display).strip() if display is not None and str(display).strip() else None
    return source_id_text, display_text


def _identity_key(source_id: str | None, display: str | None, ordinal: int) -> str:
    if source_id:
        return f"id:{source_id}"
    if display:
        return f"name:{display.casefold()}"
    return f"unknown:{ordinal}"


def _participant_aliases(
    rows: Sequence[Mapping[str, Any]],
    *,
    owner_source_id: str | None,
    owner_display_name: str | None,
) -> tuple[dict[str, str], dict[str, set[str]]]:
    owner_id = str(owner_source_id).strip() if owner_source_id and str(owner_source_id).strip() else None
    owner_name = owner_display_name.strip().casefold() if owner_display_name and owner_display_name.strip() else None

    keys_in_order: list[str] = []
    names_by_key: dict[str, set[str]] = {}
    source_by_key: dict[str, str | None] = {}

    for ordinal, row in enumerate(rows, start=1):
        source_id, display = _source_actor(row)
        if not source_id and not display:
            continue
        key = _identity_key(source_id, display, ordinal)
        if key not in names_by_key:
            keys_in_order.append(key)
            names_by_key[key] = set()
            source_by_key[key] = source_id
        if display:
            names_by_key[key].add(display)

    owner_keys: set[str] = set()
    for key in keys_in_order:
        source_id = source_by_key.get(key)
        names = names_by_key.get(key, set())
        if owner_id and source_id == owner_id:
            owner_keys.add(key)
            continue
        if owner_name and any(name.casefold() == owner_name for name in names):
            owner_keys.add(key)

    aliases: dict[str, str] = {}
    member_index = 1
    for key in keys_in_order:
        if key in owner_keys:
            aliases[key] = "admin"
        else:
            aliases[key] = f"member_{member_index:03d}"
            member_index += 1
    return aliases, names_by_key


def _name_replacements(
    aliases: Mapping[str, str],
    names_by_key: Mapping[str, set[str]],
) -> list[tuple[re.Pattern[str], str]]:
    display_pairs: list[tuple[str, str]] = []
    first_name_owners: dict[str, set[str]] = {}

    for key, alias in aliases.items():
        for display in names_by_key.get(key, set()):
            cleaned = _SPACE_RE.sub(" ", display).strip()
            if not cleaned:
                continue
            display_pairs.append((cleaned, alias))
            first = cleaned.split(" ", 1)[0].strip(".,:;!?()[]{}\"'«»")
            if len(first) >= 3 and any(ch.isalpha() for ch in first):
                first_name_owners.setdefault(first.casefold(), set()).add(alias)

    replacements: list[tuple[re.Pattern[str], str]] = []
    seen: set[tuple[str, str]] = set()
    for name, alias in sorted(display_pairs, key=lambda item: len(item[0]), reverse=True):
        marker = (name.casefold(), alias)
        if marker in seen:
            continue
        seen.add(marker)
        replacements.append((re.compile(re.escape(name), flags=re.IGNORECASE), alias))

    for first_folded, owners in sorted(first_name_owners.items(), key=lambda item: len(item[0]), reverse=True):
        if len(owners) != 1:
            continue
        alias = next(iter(owners))
        pattern = re.compile(rf"(?<!\w){re.escape(first_folded)}(?!\w)", flags=re.IGNORECASE)
        replacements.append((pattern, alias))
    return replacements


def _sanitize_text(text: str, replacements: Iterable[tuple[re.Pattern[str], str]]) -> str:
    value = text
    value = _URL_RE.sub("[link]", value)
    value = _TELEGRAM_LINK_RE.sub("[link]", value)
    value = _EMAIL_RE.sub("[email]", value)
    value = _HANDLE_RE.sub("[handle]", value)
    value = _PHONE_RE.sub("[phone]", value)
    value = _LONG_NUMBER_RE.sub("[number]", value)
    for pattern, alias in replacements:
        value = pattern.sub(alias, value)
    return value.strip()


def _safe_media(row: Mapping[str, Any]) -> dict[str, Any] | None:
    has_media = any(
        key in row
        for key in (
            "file",
            "photo",
            "media_type",
            "mime_type",
            "sticker_emoji",
            "duration_seconds",
        )
    )
    if not has_media:
        return None

    media: dict[str, Any] = {"present": True}
    for key in ("media_type", "mime_type", "duration_seconds", "width", "height", "sticker_emoji"):
        value = row.get(key)
        if isinstance(value, (str, int, float)) and value not in ("", None):
            media[key] = value
    return media


def _safe_reactions(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    reactions = row.get("reactions")
    if not isinstance(reactions, list):
        return output
    for item in reactions:
        if not isinstance(item, Mapping):
            continue
        count = item.get("count")
        emoji = item.get("emoji")
        safe: dict[str, Any] = {}
        if isinstance(emoji, str) and emoji:
            safe["emoji"] = emoji
        if isinstance(count, int) and count >= 0:
            safe["count"] = count
        if safe:
            output.append(safe)
    return output


def sanitize_telegram_export(
    payload: Mapping[str, Any],
    *,
    options: GroupLabOptions | None = None,
) -> dict[str, Any]:
    """Create a deterministic lab-only Telegram dataset without raw participant identifiers."""
    opts = options or GroupLabOptions()
    raw_rows = payload.get("messages")
    if not isinstance(raw_rows, list):
        raise ValueError("Telegram export must contain a messages list")

    rows = [row for row in raw_rows if isinstance(row, Mapping)]
    aliases, names_by_key = _participant_aliases(
        rows,
        owner_source_id=opts.owner_source_id,
        owner_display_name=opts.owner_display_name,
    )
    replacements = _name_replacements(aliases, names_by_key)

    id_map: dict[str, str] = {}
    for index, row in enumerate(rows, start=1):
        source_id = row.get("id")
        if source_id is not None:
            id_map[str(source_id)] = f"m{index:06d}"

    sanitized_messages: list[dict[str, Any]] = []
    for ordinal, row in enumerate(rows, start=1):
        kind = str(row.get("type") or "message")
        if kind == "service" and not opts.include_service_messages:
            continue

        source_actor_id, display = _source_actor(row)
        actor_key = _identity_key(source_actor_id, display, ordinal)
        actor_alias = aliases.get(actor_key)

        source_id = row.get("id")
        lab_id = id_map.get(str(source_id), f"m{ordinal:06d}")
        reply_source = row.get("reply_to_message_id")
        reply_lab_id = id_map.get(str(reply_source)) if reply_source is not None else None

        text = _sanitize_text(_flatten_telegram_text(row.get("text")), replacements)
        entry: dict[str, Any] = {
            "id": lab_id,
            "kind": kind,
            "occurred_at": _iso_timestamp(row.get("date")),
            "actor": actor_alias,
            "text": text,
            "reply_to": reply_lab_id,
        }

        edited_at = _iso_timestamp(row.get("edited"))
        if edited_at:
            entry["edited_at"] = edited_at

        if kind == "service":
            action = row.get("action")
            if isinstance(action, str) and action:
                entry["service_action"] = action

        media = _safe_media(row)
        if media is not None:
            entry["media"] = media

        reactions = _safe_reactions(row)
        if reactions:
            entry["reactions"] = reactions

        sanitized_messages.append(entry)

    timestamps = [item["occurred_at"] for item in sanitized_messages if item.get("occurred_at")]
    return {
        "lab_dataset_version": LAB_DATASET_VERSION,
        "source": {
            "kind": "telegram_desktop_export",
            "group_type": str(payload.get("type") or "unknown"),
        },
        "stats": {
            "message_count": len(sanitized_messages),
            "participant_count": len(set(aliases.values())),
            "first_timestamp": min(timestamps) if timestamps else None,
            "last_timestamp": max(timestamps) if timestamps else None,
        },
        "messages": sanitized_messages,
    }


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_frozen_replay(
    dataset: Mapping[str, Any],
    *,
    options: ReplayOptions | None = None,
) -> dict[str, Any]:
    """Build deterministic review/replay cases without invoking models or live services."""
    opts = options or ReplayOptions()
    opts.validate()

    raw_messages = dataset.get("messages")
    if not isinstance(raw_messages, list):
        raise ValueError("sanitized dataset must contain a messages list")

    messages = [dict(item) for item in raw_messages if isinstance(item, Mapping)]
    episodes: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    previous_dt: datetime | None = None

    for item in messages:
        item_dt = _parse_dt(item.get("occurred_at"))
        gap_break = False
        if current and previous_dt is not None and item_dt is not None:
            gap_seconds = (item_dt - previous_dt).total_seconds()
            gap_break = gap_seconds > opts.inactivity_gap_minutes * 60
        cap_break = len(current) >= opts.max_episode_messages

        if current and (gap_break or cap_break):
            episodes.append(current)
            current = []
        current.append(item)
        if item_dt is not None:
            previous_dt = item_dt

    if current:
        episodes.append(current)

    frozen_episodes: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []
    case_index = 1

    for episode_index, episode in enumerate(episodes, start=1):
        episode_id = f"e{episode_index:05d}"
        frozen_episodes.append(
            {
                "id": episode_id,
                "message_ids": [item.get("id") for item in episode],
                "message_count": len(episode),
                "started_at": episode[0].get("occurred_at") if episode else None,
                "ended_at": episode[-1].get("occurred_at") if episode else None,
            }
        )

        for position, item in enumerate(episode):
            if item.get("kind") != "message":
                continue
            if not str(item.get("text") or "").strip() and not item.get("media"):
                continue
            start = max(0, position - opts.context_messages)
            context = episode[start:position]
            cases.append(
                {
                    "id": f"c{case_index:06d}",
                    "episode_id": episode_id,
                    "context": context,
                    "current": item,
                    "review_label": "uncertain",
                    "expected_bot_text": None,
                }
            )
            case_index += 1

    return {
        "lab_replay_version": LAB_REPLAY_VERSION,
        "dataset_version": dataset.get("lab_dataset_version"),
        "options": {
            "inactivity_gap_minutes": opts.inactivity_gap_minutes,
            "max_episode_messages": opts.max_episode_messages,
            "context_messages": opts.context_messages,
        },
        "stats": {
            "episode_count": len(frozen_episodes),
            "case_count": len(cases),
        },
        "episodes": frozen_episodes,
        "cases": cases,
    }


def canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, separators=(",", ": "))
        + "\n"
    ).encode("utf-8")


def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object")
    return value


def write_json(path: str | Path, payload: Mapping[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(canonical_json_bytes(payload))
