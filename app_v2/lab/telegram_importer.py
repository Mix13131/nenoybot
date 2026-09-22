from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app_v2.lab.models import (
    CanonicalLabDataset,
    CanonicalLabEvent,
    LabMedia,
    LabReaction,
)


_URL_RE = re.compile(r"https?://[^\s<>]+", flags=re.IGNORECASE)
_EMAIL_RE = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    flags=re.IGNORECASE,
)
_HANDLE_RE = re.compile(r"(?<!\w)@[A-Za-z0-9_]{5,32}\b")
_PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{8,}\d(?!\w)")
_DIGIT_SEQUENCE_RE = re.compile(r"(?<!\d)(?:\d[\s-]?){13,19}(?!\d)")
_ACCESS_CODE_RE = re.compile(
    r"(?i)\b(?:код\s+доступа|парол[ья]|passcode|password)\s*[:：-]?\s*"
    r"[A-Za-zА-Яа-я0-9#*._-]{3,}"
)
_MEETING_ID_RE = re.compile(
    r"(?i)\b(?:идентификатор\s+конференции|meeting\s+id)\s*[:：-]?\s*"
    r"[\d\s-]{6,}"
)
_PAYMENT_LINE_RE = re.compile(
    r"(?im)^([^\n]*(?:реквизит|оплат|перевод|карт[аы])[^\n]*"
    r"(?:\d[\d\s-]{10,}\d)[^\n]*)$"
)
_NAME_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё]{3,}")
_SAFE_LABEL_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "dclid",
    "msclkid",
    "yclid",
    "ttclid",
    "twclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "mkt_tok",
    "_hsenc",
    "_hsmi",
    "vero_conv",
    "vero_id",
}


@dataclass(frozen=True)
class TelegramExportImportConfig:
    label: str
    owner_source_ids: frozenset[str] = frozenset()
    owner_display_names: frozenset[str] = frozenset()


class _AliasBook:
    def __init__(self, config: TelegramExportImportConfig) -> None:
        self.config = config
        self._aliases: dict[str, str] = {}
        self._display_to_alias: dict[str, str] = {}
        self._owner_counter = 0
        self._user_counter = 0
        self._channel_counter = 0
        self._actor_counter = 0
        self._anonymous_counter = 0

    @staticmethod
    def _norm_name(value: Any) -> str:
        return " ".join(str(value or "").split()).strip().casefold()

    def alias_for(
        self,
        source_id: Any,
        display_name: Any = None,
        *,
        kind_hint: str | None = None,
    ) -> str | None:
        source = str(source_id or "").strip()
        display = " ".join(str(display_name or "").split()).strip()
        if not source and not display:
            return None

        key = source or f"name:{self._norm_name(display)}"
        existing = self._aliases.get(key)
        if existing:
            if display:
                self._display_to_alias.setdefault(self._norm_name(display), existing)
            return existing

        # A service event may mention a member by display name before the same
        # person later appears with a stable Telegram source id. Reuse that
        # name-only alias instead of inventing a second identity.
        display_key = self._norm_name(display)
        if source and display_key:
            by_name = self._display_to_alias.get(display_key)
            if by_name:
                self._aliases[key] = by_name
                return by_name

        is_owner = (
            source in self.config.owner_source_ids
            or self._norm_name(display)
            in {self._norm_name(name) for name in self.config.owner_display_names}
        )
        if is_owner:
            self._owner_counter += 1
            alias = f"owner_{self._owner_counter:03d}"
        elif source.startswith("user"):
            self._user_counter += 1
            alias = f"user_{self._user_counter:03d}"
        elif source.startswith("channel") or kind_hint == "channel":
            self._channel_counter += 1
            alias = f"channel_{self._channel_counter:03d}"
        elif source:
            self._actor_counter += 1
            alias = f"actor_{self._actor_counter:03d}"
        else:
            self._anonymous_counter += 1
            alias = f"person_{self._anonymous_counter:03d}"

        self._aliases[key] = alias
        if display:
            self._display_to_alias.setdefault(self._norm_name(display), alias)
        return alias

    def register_name(
        self,
        display_name: Any,
        alias: str | None = None,
    ) -> str | None:
        display = " ".join(str(display_name or "").split()).strip()
        if not display:
            return alias
        display_key = self._norm_name(display)
        if alias is None:
            existing = self._display_to_alias.get(display_key)
            if existing:
                return existing
        resolved = alias or self.alias_for(None, display)
        if resolved:
            self._display_to_alias.setdefault(display_key, resolved)
        return resolved

    def name_replacements(self) -> dict[str, str]:
        return dict(self._display_to_alias)

    @property
    def participant_count(self) -> int:
        return len(set(self._aliases.values()))


class TelegramExportImporter:
    """Convert Telegram Desktop JSON exports into safe replay fixtures.

    This is deliberately offline deterministic code: no LLM calls, no network
    requests and no production-memory writes.
    """

    def __init__(self, config: TelegramExportImportConfig) -> None:
        label = str(config.label or "").strip()
        if not label:
            raise ValueError("label is required")
        if not _SAFE_LABEL_RE.fullmatch(label):
            raise ValueError(
                "label must be a privacy-safe lowercase slug "
                "(a-z, 0-9, underscore, hyphen; max 64 chars)"
            )
        self.config = TelegramExportImportConfig(
            label=label,
            owner_source_ids=frozenset(
                str(item).strip()
                for item in config.owner_source_ids
                if str(item).strip()
            ),
            owner_display_names=frozenset(
                " ".join(str(item).split()).strip()
                for item in config.owner_display_names
                if " ".join(str(item).split()).strip()
            ),
        )
        self.aliases = _AliasBook(self.config)
        self.redactions: Counter[str] = Counter()

    def import_path(self, path: str | Path) -> CanonicalLabDataset:
        source = Path(path)
        payload = json.loads(source.read_text(encoding="utf-8"))
        return self.import_payload(payload)

    def import_payload(self, payload: Any) -> CanonicalLabDataset:
        if not isinstance(payload, dict):
            raise ValueError("Telegram export root must be an object")
        messages = payload.get("messages")
        if not isinstance(messages, list):
            raise ValueError("Telegram export must contain a messages list")

        self._pre_register_participants(messages)
        replacements = self._build_name_replacements()
        source_title = " ".join(str(payload.get("name") or "").split()).strip()
        if source_title:
            # Root chat identity is source metadata, never model-visible data.
            replacements[source_title.casefold()] = "[SOURCE_CHAT]"

        events: list[CanonicalLabEvent] = []
        message_count = 0
        service_count = 0
        for raw in messages:
            if not isinstance(raw, dict):
                continue
            event_type = str(raw.get("type") or "").strip()
            if event_type not in {"message", "service"}:
                continue
            event = self._event(raw, replacements)
            if event is None:
                continue
            events.append(event)
            if event_type == "message":
                message_count += 1
            else:
                service_count += 1

        digest = hashlib.sha256(
            (
                self.config.label
                + "\n"
                + "\n".join(
                    f"{event.source_message_id}:{event.occurred_at.isoformat()}"
                    for event in events
                )
            ).encode("utf-8")
        ).hexdigest()[:20]
        dataset_id = f"lab_{digest}"

        stats = {
            "event_count": len(events),
            "message_count": message_count,
            "service_count": service_count,
            "participant_count": self.aliases.participant_count,
            "reaction_count": sum(
                reaction.count
                for event in events
                for reaction in event.reactions
            ),
            "redactions": dict(sorted(self.redactions.items())),
        }
        return CanonicalLabDataset(
            dataset_id=dataset_id,
            label=self.config.label,
            source_kind="telegram_desktop_json",
            source_chat_type=self._safe_chat_type(payload.get("type")),
            events=tuple(events),
            stats=stats,
        )

    def _pre_register_participants(self, messages: list[Any]) -> None:
        # Pass 1: register every identity that has a stable Telegram source id
        # anywhere in the export. This makes aliases independent of whether a
        # service member-name mention happens before the person's first message.
        for raw in messages:
            if not isinstance(raw, dict):
                continue
            for source_id, display_name, kind_hint in (
                (raw.get("from_id"), raw.get("from"), None),
                (
                    raw.get("actor_id"),
                    raw.get("actor"),
                    "channel"
                    if str(raw.get("actor_id") or "").startswith("channel")
                    else None,
                ),
                (
                    raw.get("forwarded_from_id"),
                    raw.get("forwarded_from"),
                    None,
                ),
            ):
                if str(source_id or "").strip():
                    self.aliases.alias_for(
                        source_id,
                        display_name,
                        kind_hint=kind_hint,
                    )
            for reaction in raw.get("reactions") or ():
                if not isinstance(reaction, dict):
                    continue
                for recent in reaction.get("recent") or ():
                    if (
                        isinstance(recent, dict)
                        and str(recent.get("from_id") or "").strip()
                    ):
                        self.aliases.alias_for(
                            recent.get("from_id"),
                            recent.get("from"),
                        )

        # Pass 2: attach name-only references to already known identities where
        # possible; allocate person aliases only for identities that truly have
        # no source id anywhere in the export.
        for raw in messages:
            if not isinstance(raw, dict):
                continue
            for source_id, display_name in (
                (raw.get("from_id"), raw.get("from")),
                (raw.get("actor_id"), raw.get("actor")),
                (raw.get("forwarded_from_id"), raw.get("forwarded_from")),
            ):
                if not str(source_id or "").strip():
                    self.aliases.register_name(display_name)
            for member in raw.get("members") or ():
                self.aliases.register_name(member)
            for reaction in raw.get("reactions") or ():
                if not isinstance(reaction, dict):
                    continue
                for recent in reaction.get("recent") or ():
                    if (
                        isinstance(recent, dict)
                        and not str(recent.get("from_id") or "").strip()
                    ):
                        self.aliases.register_name(recent.get("from"))

    def _build_name_replacements(self) -> dict[str, str]:
        replacements = self.aliases.name_replacements()
        token_targets: dict[str, set[str]] = {}
        for display, alias in list(replacements.items()):
            for token in _NAME_TOKEN_RE.findall(display):
                token_targets.setdefault(token.casefold(), set()).add(alias)

        for token, aliases in token_targets.items():
            if len(aliases) == 1:
                replacements.setdefault(token, next(iter(aliases)))
            else:
                # Ambiguous first names still must not leak from the source.
                replacements.setdefault(token, "[PERSON]")
        return replacements

    def _event(
        self,
        raw: dict[str, Any],
        replacements: dict[str, str],
    ) -> CanonicalLabEvent | None:
        source_message_id = self._int_or_none(raw.get("id"))
        if source_message_id is None:
            return None
        occurred_at = self._timestamp(raw.get("date"), raw.get("date_unixtime"))
        if occurred_at is None:
            return None

        event_type = str(raw.get("type") or "")
        if event_type == "service":
            actor_alias = self.aliases.alias_for(
                raw.get("actor_id"),
                raw.get("actor"),
                kind_hint="channel"
                if str(raw.get("actor_id") or "").startswith("channel")
                else None,
            )
        else:
            actor_alias = self.aliases.alias_for(
                raw.get("from_id"),
                raw.get("from"),
            )
        actor_role = "owner" if actor_alias and actor_alias.startswith("owner_") else None

        text = self._flatten_text(raw.get("text"))
        text = self._sanitize_text(text, replacements)

        forwarded_alias = self.aliases.alias_for(
            raw.get("forwarded_from_id"),
            raw.get("forwarded_from"),
        )
        media = self._media(raw)
        reactions = self._reactions(raw.get("reactions"))

        metadata: dict[str, Any] = {}
        if event_type == "service":
            schedule_at = self._timestamp(None, raw.get("schedule_date"))
            if schedule_at:
                metadata["schedule_at"] = schedule_at.isoformat()
            members = raw.get("members")
            if isinstance(members, list):
                metadata["member_count"] = len(members)
        if raw.get("edited") or raw.get("edited_unixtime"):
            metadata["edited"] = True

        return CanonicalLabEvent(
            source_message_id=source_message_id,
            event_type=event_type,
            occurred_at=occurred_at,
            actor_alias=actor_alias,
            actor_role=actor_role,
            text=text,
            reply_to_source_message_id=self._int_or_none(
                raw.get("reply_to_message_id")
            ),
            edited_at=self._timestamp(
                raw.get("edited"),
                raw.get("edited_unixtime"),
            ),
            service_action=(
                str(raw.get("action") or "").strip() or None
                if event_type == "service"
                else None
            ),
            forwarded_actor_alias=forwarded_alias,
            media=media,
            reactions=reactions,
            metadata=metadata or None,
        )

    def _sanitize_text(
        self,
        text: str,
        replacements: dict[str, str],
    ) -> str:
        value = text
        value = _PAYMENT_LINE_RE.sub(
            self._redaction_replacer(
                "payment_details",
                "[REDACTED_PAYMENT_DETAILS]",
            ),
            value,
        )
        value = _ACCESS_CODE_RE.sub(
            self._redaction_replacer(
                "access_code",
                "[REDACTED_ACCESS_CODE]",
            ),
            value,
        )
        value = _MEETING_ID_RE.sub(
            self._redaction_replacer(
                "meeting_id",
                "[REDACTED_MEETING_ID]",
            ),
            value,
        )
        value = _URL_RE.sub(self._sanitize_url_match, value)
        value = _EMAIL_RE.sub(
            self._redaction_replacer("email", "[REDACTED_EMAIL]"),
            value,
        )
        value = _HANDLE_RE.sub(
            self._redaction_replacer("handle", "[REDACTED_HANDLE]"),
            value,
        )
        value = _PHONE_RE.sub(
            self._redaction_replacer("phone", "[REDACTED_PHONE]"),
            value,
        )
        value = _DIGIT_SEQUENCE_RE.sub(self._sanitize_long_number_match, value)

        for source, alias in sorted(
            replacements.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            if not source:
                continue
            pattern = re.compile(
                rf"(?<![\w]){re.escape(source)}(?![\w])",
                flags=re.IGNORECASE,
            )
            value, count = pattern.subn(alias, value)
            if count:
                self.redactions["person_name"] += count

        return "\n".join(
            line.rstrip()
            for line in value.splitlines()
        ).strip()

    def _sanitize_url_match(self, match: re.Match[str]) -> str:
        raw = match.group(0).rstrip(".,);]")
        trailing = match.group(0)[len(raw):]
        try:
            parsed = urlsplit(raw)
        except ValueError:
            self.redactions["url"] += 1
            return "[REDACTED_URL]" + trailing

        host = (parsed.hostname or "").casefold()
        path = parsed.path.casefold()
        query_keys = {key.casefold() for key, _ in parse_qsl(parsed.query)}
        sensitive = False
        marker = "[REDACTED_CREDENTIAL_URL]"
        if "zoom.us" in host and ("/j/" in path or "pwd" in query_keys):
            sensitive = True
            marker = "[REDACTED_MEETING_LINK]"
        elif host.endswith("t.me") and (
            parsed.path.startswith("/+")
            or "/joinchat/" in path
        ):
            sensitive = True
            marker = "[REDACTED_INVITE_LINK]"
        elif host.endswith("vk.com") and "/call/join/" in path:
            sensitive = True
            marker = "[REDACTED_CALL_LINK]"
        elif query_keys & {
            "token",
            "auth",
            "key",
            "pwd",
            "passcode",
            "signature",
            "sig",
        }:
            sensitive = True

        if sensitive:
            self.redactions["credential_url"] += 1
            return marker + trailing

        # Public resources are useful for later material-index evaluation, but
        # strip query/fragment trackers from the canonical fixture.
        safe_query = urlencode(
            [
                (key, value)
                for key, value in parse_qsl(parsed.query)
                if not (
                    key.casefold().startswith("utm_")
                    or key.casefold() in _TRACKING_QUERY_KEYS
                )
            ]
        )
        return urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, safe_query, "")
        ) + trailing

    def _sanitize_long_number_match(self, match: re.Match[str]) -> str:
        raw = match.group(0)
        digits = re.sub(r"\D", "", raw)
        if 13 <= len(digits) <= 19 and self._luhn_ok(digits):
            self.redactions["payment_card"] += 1
            return "[REDACTED_PAYMENT_CARD]"
        return raw

    def _redaction_replacer(self, kind: str, marker: str):
        def replace(match: re.Match[str]) -> str:
            self.redactions[kind] += 1
            return marker

        return replace

    @staticmethod
    def _luhn_ok(digits: str) -> bool:
        total = 0
        parity = len(digits) % 2
        for index, char in enumerate(digits):
            value = int(char)
            if index % 2 == parity:
                value *= 2
                if value > 9:
                    value -= 9
            total += value
        return total % 10 == 0

    @staticmethod
    def _flatten_text(value: Any) -> str:
        if isinstance(value, str):
            return value
        if not isinstance(value, list):
            return ""
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
        return "".join(parts)

    @staticmethod
    def _media(raw: dict[str, Any]) -> LabMedia | None:
        media_type = str(raw.get("media_type") or "").strip() or None
        mime_type = str(raw.get("mime_type") or "").strip() or None
        file_name = str(raw.get("file_name") or "").strip()
        has_media = bool(
            media_type
            or mime_type
            or file_name
            or raw.get("photo")
            or raw.get("file")
            or raw.get("sticker_emoji")
        )
        if not has_media:
            return None
        extension = None
        if file_name:
            suffix = Path(file_name).suffix.lower()
            if re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
                extension = suffix
        return LabMedia(
            media_type=media_type or (
                "photo" if raw.get("photo") else "file"
            ),
            mime_type=mime_type,
            file_extension=extension,
            file_size=TelegramExportImporter._int_or_none(
                raw.get("file_size") or raw.get("photo_file_size")
            ),
            duration_seconds=TelegramExportImporter._int_or_none(
                raw.get("duration_seconds")
            ),
            width=TelegramExportImporter._int_or_none(raw.get("width")),
            height=TelegramExportImporter._int_or_none(raw.get("height")),
            sticker_emoji=str(raw.get("sticker_emoji") or "").strip() or None,
        )

    @staticmethod
    def _reactions(value: Any) -> tuple[LabReaction, ...]:
        if not isinstance(value, list):
            return ()
        result: list[LabReaction] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            emoji = str(item.get("emoji") or "").strip()
            count = TelegramExportImporter._int_or_none(item.get("count"))
            if not emoji or count is None or count <= 0:
                continue
            result.append(LabReaction(emoji=emoji, count=count))
        return tuple(result)

    @staticmethod
    def _timestamp(value: Any, unix_value: Any) -> datetime | None:
        if unix_value is not None:
            try:
                return datetime.fromtimestamp(
                    int(str(unix_value)),
                    tz=timezone.utc,
                )
            except (TypeError, ValueError, OSError):
                pass
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            # Telegram Desktop export timestamps are local-looking strings
            # while date_unixtime is authoritative when present. When only the
            # ISO value exists, preserve the wall time but make it explicit UTC
            # instead of leaving ambiguous naive datetimes in replay fixtures.
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_chat_type(value: Any) -> str:
        raw = str(value or "").strip()
        return raw if raw in {
            "private_supergroup",
            "public_supergroup",
            "private_group",
            "public_group",
            "channel",
        } else "telegram_chat"


def write_dataset(dataset: CanonicalLabDataset, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(dataset.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import a Telegram Desktop JSON export into a safe Group Lab dataset"
    )
    parser.add_argument("input_json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument(
        "--owner-name",
        action="append",
        default=[],
        help="Display name(s) treated as the community owner; repeatable",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    importer = TelegramExportImporter(
        TelegramExportImportConfig(
            label=args.label,
            owner_display_names=frozenset(args.owner_name),
        )
    )
    dataset = importer.import_path(args.input_json)
    write_dataset(dataset, args.output)
    print(
        json.dumps(
            {
                "ok": True,
                "dataset_id": dataset.dataset_id,
                "label": dataset.label,
                "stats": dataset.stats,
                "output": str(Path(args.output)),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
