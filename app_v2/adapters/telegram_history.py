"""Strict, offline projection of a Telegram Desktop JSON export.

An export is a snapshot, not a stream of Telegram updates. Records deliberately
have no ``telegram_update_id``. Topic membership is accepted only from explicit
fields, a reply chain ending at an exported topic-creation event, or an operator's
separately confirmed thread. Names, reactions and local media paths are not source
identity. No file, network, model or database access occurs in this module.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Literal


_MAX_ID = (1 << 63) - 1
_SUPERGROUP_OFFSET = 1_000_000_000_000
_MAX_BYTES = 8 * 1024 * 1024
_MAX_MESSAGES = 100_000
_MAX_TEXT = 1_000_000
_MAX_DEPTH = 64
_SUPERGROUP_TYPES = frozenset({"public_supergroup", "private_supergroup"})
_GROUP_TYPES = _SUPERGROUP_TYPES | {"private_group"}
_TEXT_WRAPPERS = frozenset({
    "bold", "italic", "underline", "strikethrough", "fixed", "code", "spoiler",
    "url", "email", "phone", "anchor", "mention", "custom_emoji", "subscript",
    "superscript", "marked", "text_link", "hashtag", "cashtag", "bank_card",
    "bot_command", "pre", "link", "mention_name", "phone_number",
})
_QUOTES = frozenset({
    "blockquote", "expandable_blockquote", "block_quote", "quote",
    "block_quotation", "expandable_block_quotation",
})
_EXTERNAL_FIELDS = frozenset({
    "forwarded_from", "forwarded_from_id", "saved_from", "via_bot", "quote",
    "external_reply",
})


class HistoryExportError(ValueError):
    """Invalid or contradictory export; messages never include source content."""


def _id(value: object, label: str = "identifier") -> int:
    if type(value) is not int or not 1 <= value <= _MAX_ID:
        raise HistoryExportError(f"{label} must be a positive 64-bit integer")
    return value


def _utc(value: object, label: str) -> datetime:
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,12}", value):
        value = int(value)
    _id(value, label)
    try:
        return datetime.fromtimestamp(value, timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise HistoryExportError(f"{label} is outside the supported date range") from exc


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise HistoryExportError("export must contain finite JSON values") from exc


class _FrozenReference(dict):
    def _immutable(self, *args: object, **kwargs: object) -> None:
        raise TypeError("media_reference is immutable")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _immutable
    __ior__ = _immutable

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


@dataclass(frozen=True)
class HistoricalMessage:
    chat_id: int
    message_thread_id: int
    telegram_message_id: int
    author_user_id: int
    occurred_at: datetime
    revision_at: datetime
    is_edited: bool
    reply_to_message_id: int | None
    content_kind: str
    available_text: str | None
    body_coverage: Literal["full", "partial", "unavailable"]
    media_reference: dict[str, str | int]
    contains_external_content: bool

    def __post_init__(self) -> None:
        if type(self.chat_id) is not int or not -_MAX_ID <= self.chat_id < 0:
            raise HistoryExportError("chat_id must be a negative Telegram group integer")
        for name in ("message_thread_id", "telegram_message_id", "author_user_id"):
            _id(getattr(self, name), name)
        if self.reply_to_message_id is not None:
            _id(self.reply_to_message_id, "reply_to_message_id")
        for name in ("is_edited", "contains_external_content"):
            if type(getattr(self, name)) is not bool:
                raise HistoryExportError(f"{name} must be a bool")
        for name in ("occurred_at", "revision_at"):
            value = getattr(self, name)
            if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
                raise HistoryExportError(f"{name} must be a UTC-aware datetime")
            object.__setattr__(self, name, value.astimezone(timezone.utc))
        if self.occurred_at <= datetime(1970, 1, 1, tzinfo=timezone.utc):
            raise HistoryExportError("occurred_at must be after the Unix epoch")
        if self.revision_at < self.occurred_at:
            raise HistoryExportError("revision_at cannot precede occurred_at")
        if not self.is_edited and self.revision_at != self.occurred_at:
            raise HistoryExportError("an unedited source has one timestamp")
        if self.content_kind not in {
            "text", "photo", "document", "audio", "video", "voice", "animation",
            "sticker", "video_note", "rich_message", "unsupported",
        }:
            raise HistoryExportError("unsupported content kind")
        if self.available_text is not None and (
            not isinstance(self.available_text, str) or len(self.available_text) > _MAX_TEXT
        ):
            raise HistoryExportError("available_text is invalid or exceeds the text bound")
        if self.body_coverage not in {"full", "partial", "unavailable"}:
            raise HistoryExportError("invalid body coverage")
        if self.body_coverage == "full" and (
            self.content_kind != "text" or self.available_text is None
        ):
            raise HistoryExportError("full coverage requires captured plain text")
        if self.body_coverage == "partial" and self.available_text is None:
            raise HistoryExportError("partial coverage requires captured text")
        if self.body_coverage == "unavailable" and self.available_text is not None:
            raise HistoryExportError("unavailable coverage cannot include captured text")
        if not isinstance(self.media_reference, dict) or self.media_reference:
            # Desktop paths are not Telegram file references and are never fetched.
            raise HistoryExportError("Desktop snapshots cannot supply Telegram media references")
        object.__setattr__(self, "media_reference", _FrozenReference())

    @property
    def source_kind(self) -> str:
        return "telegram_desktop_export"

    @property
    def checksum(self) -> str:
        """Semantic identity, independent of file name, formatting or reactions."""
        body = {
            "history_snapshot_version": 1,
            "source_kind": self.source_kind,
            "chat_id": self.chat_id,
            "message_thread_id": self.message_thread_id,
            "telegram_message_id": self.telegram_message_id,
            "author_user_id": self.author_user_id,
            "occurred_at": self.occurred_at.isoformat(timespec="microseconds"),
            "revision_at": self.revision_at.isoformat(timespec="microseconds"),
            "is_edited": self.is_edited,
            "reply_to_message_id": self.reply_to_message_id,
            "content_kind": self.content_kind,
            "available_text": self.available_text,
            "body_coverage": self.body_coverage,
            "media_reference": dict(self.media_reference),
            "contains_external_content": self.contains_external_content,
        }
        return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()

@dataclass(frozen=True)
class TelegramHistoryExport:
    chat_id: int
    export_sha256: str
    messages: tuple[HistoricalMessage, ...]
    input_messages: int
    duplicate_messages: int
    skipped_counts: dict[str, int]
    warning_counts: dict[str, int]


@dataclass
class _TextReader:
    unknown_nodes: int = 0
    external: bool = False
    consumed_characters: int = 0

    def leaf(self, value: object) -> str:
        if not isinstance(value, str):
            raise HistoryExportError("text leaf must be a string")
        try:
            value.encode("utf-8")
        except UnicodeError as exc:
            raise HistoryExportError("text must be valid Unicode") from exc
        self.consumed_characters += len(value)
        if self.consumed_characters > _MAX_TEXT:
            raise HistoryExportError("message exceeds the text bound")
        return value

    def text(self, value: object, depth: int = 0) -> str:
        if depth > _MAX_DEPTH:
            raise HistoryExportError("rich text exceeds the nesting bound")
        if isinstance(value, str):
            return self.leaf(value)
        if isinstance(value, list):
            return "".join(self.text(item, depth + 1) for item in value)
        if not isinstance(value, dict) or not isinstance(value.get("type"), str):
            raise HistoryExportError("text entity must be a typed object")
        kind = value["type"]
        if kind == "empty":
            return ""
        if kind in _QUOTES:
            self.external = True
        if kind == "plain":
            return self.leaf(value.get("text"))
        if kind == "concat":
            if not isinstance(value.get("text"), list):
                raise HistoryExportError("concat text must be a list")
            return self.text(value["text"], depth + 1)
        if kind in _TEXT_WRAPPERS or kind in _QUOTES:
            return self.text(value.get("text"), depth + 1)
        self.unknown_nodes += 1
        return ""

    def blocks(self, value: object, depth: int = 0) -> str:
        if depth > _MAX_DEPTH:
            raise HistoryExportError("rich blocks exceed the nesting bound")
        if not isinstance(value, list):
            raise HistoryExportError("rich blocks must be a list")
        lines: list[str] = []
        for block in value:
            if not isinstance(block, dict) or not isinstance(block.get("type"), str):
                raise HistoryExportError("rich block must be a typed object")
            kind = block["type"]
            if kind in _QUOTES:
                self.external = True
            if kind in {"paragraph", "title", "subtitle", "header", "subheader",
                        "footer", "preformatted"} | _QUOTES:
                lines.append(self.text(block.get("text"), depth + 1))
            elif kind == "list":
                items = block.get("items")
                if not isinstance(items, list):
                    raise HistoryExportError("rich list items must be a list")
                numbered = block.get("kind") == "ordered"
                values: list[str] = []
                for index, item in enumerate(items, 1):
                    if not isinstance(item, dict):
                        raise HistoryExportError("rich list item must be an object")
                    if item.get("content") == "blocks":
                        item_text = self.blocks(item.get("blocks"), depth + 1)
                    elif item.get("content", "text") == "text":
                        item_text = self.text(item.get("text"), depth + 1)
                    else:
                        self.unknown_nodes += 1
                        continue
                    if numbered:
                        number = item.get("num", str(index))
                        if not isinstance(number, str) or not re.fullmatch(r"[0-9]{1,9}", number):
                            raise HistoryExportError("ordered list number must be numeric")
                        prefix = number + ". "
                    else:
                        prefix = "- "
                    if item.get("task_state") == "checked":
                        prefix += "[x] "
                    elif item.get("task_state") == "unchecked":
                        prefix += "[ ] "
                    values.append(prefix + item_text)
                lines.append("\n".join(values))
            elif kind == "divider":
                lines.append("")
            else:
                # Unknown media/widget fields are not mistaken for human text.
                self.unknown_nodes += 1
        return "\n\n".join(lines)


def _duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise HistoryExportError("duplicate JSON key")
        result[key] = value
    return result


def _decode(payload: bytes | str | dict[str, Any]) -> tuple[dict[str, Any], str]:
    if isinstance(payload, dict):
        try:
            encoded = _canonical(payload).encode("utf-8")
        except UnicodeError as exc:
            raise HistoryExportError("export must be valid Unicode") from exc
        data = payload
    elif isinstance(payload, (str, bytes)):
        try:
            encoded = payload.encode("utf-8") if isinstance(payload, str) else payload
        except UnicodeError as exc:
            raise HistoryExportError("export must be valid Unicode") from exc
        if len(encoded) > _MAX_BYTES:
            raise HistoryExportError("export exceeds the file-size bound")
        try:
            data = json.loads(encoded.decode("utf-8-sig"), object_pairs_hook=_duplicate_keys,
                              parse_constant=lambda _: (_ for _ in ()).throw(
                                  HistoryExportError("non-finite JSON value")))
        except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
            raise HistoryExportError("export is not valid UTF-8 JSON") from exc
    else:
        raise HistoryExportError("export must be a JSON object, UTF-8 bytes or string")
    if len(encoded) > _MAX_BYTES:
        raise HistoryExportError("export exceeds the file-size bound")
    if not isinstance(data, dict):
        raise HistoryExportError("export root must be an object")
    return data, hashlib.sha256(encoded).hexdigest()


def _threads(index: dict[int, dict[str, Any]], confirmed: int | None,
             *, ordinary_group: bool) -> dict[int, int | None]:
    resolved: dict[int, int | None] = {}
    for key, message in index.items():
        declared = [message[field] for field in ("message_thread_id", "topic_id", "reply_to_top_id")
                    if field in message]
        for item in declared:
            _id(item, "topic identifier")
        if ordinary_group and (any(item != 1 for item in declared) or
                               message.get("action") == "topic_created"):
            raise HistoryExportError("ordinary group cannot claim a forum topic")
        if len(set(declared)) > 1:
            raise HistoryExportError("conflicting explicit topic identifiers")
        if message.get("type") == "service" and message.get("action") == "topic_created":
            if declared and declared[0] != key:
                raise HistoryExportError("topic creation identifier conflicts with topic")
            resolved[key] = key
        elif declared:
            resolved[key] = declared[0]
    for first in index:
        path: list[int] = []
        seen: set[int] = set()
        current = first
        while current not in resolved:
            if current in seen:
                raise HistoryExportError("reply chain contains a cycle")
            seen.add(current)
            path.append(current)
            item = index.get(current)
            if item is None or "reply_to_message_id" not in item:
                thread = confirmed if confirmed is not None else 1 if ordinary_group else None
                break
            current = _id(item["reply_to_message_id"], "reply_to_message_id")
        else:
            thread = resolved[current]
        for key in reversed(path):
            resolved[key] = thread
    return resolved


def _body(message: dict[str, Any]) -> tuple[str, str | None, str, bool, int]:
    reader = _TextReader()
    media_type = message.get("media_type")
    if media_type is not None and not isinstance(media_type, str):
        raise HistoryExportError("media_type must be a string when present")
    media_kind = {
        "voice_message": "voice", "video_message": "video_note",
        "video_file": "video", "audio_file": "audio", "animation": "animation",
        "sticker": "sticker",
    }.get(media_type)
    if "photo" in message:
        media_kind = "photo"
    elif media_kind is None and "file" in message:
        media_kind = "document"

    if "rich_message" in message:
        rich = message["rich_message"]
        if not isinstance(rich, dict):
            raise HistoryExportError("rich_message must be an object")
        text = reader.blocks(rich.get("blocks"))
        if message.get("text") not in (None, "", []):
            raise HistoryExportError("message has competing plain and rich text bodies")
        kind = "rich_message"
        coverage = "partial" if text else "unavailable"
        available = text or None
    elif "text" in message:
        text = reader.text(message["text"])
        kind = media_kind or "text"
        available = text if text or (media_kind is None and not reader.unknown_nodes) else None
        coverage = ("unavailable" if available is None else
                    "partial" if media_kind or reader.unknown_nodes else "full")
    else:
        kind, available, coverage = media_kind or "unsupported", None, "unavailable"

    entities = message.get("text_entities", [])
    if not isinstance(entities, list):
        raise HistoryExportError("text_entities must be a list")
    if any(isinstance(item, dict) and item.get("type") in _QUOTES for item in entities):
        reader.external = True
    external = reader.external or bool(_EXTERNAL_FIELDS.intersection(message))
    return kind, available, coverage, external, reader.unknown_nodes


def parse_telegram_desktop_export(
    payload: bytes | str | dict[str, Any], *, expected_chat_id: int | None = None,
    confirmed_thread_id: int | None = None,
) -> TelegramHistoryExport:
    """Parse one exported group with explicit provenance and loss accounting.

    ``confirmed_thread_id`` is an operator assertion for otherwise unresolved
    messages, never an automatic default for a supergroup. Exported ``user`` IDs
    establish identity, not whether an account is a bot; the importing service
    must verify account/membership permissions before granting history access.
    """
    data, digest = _decode(payload)
    chat_type = data.get("type")
    if not isinstance(chat_type, str) or chat_type not in _GROUP_TYPES:
        raise HistoryExportError("only group exports are supported")
    raw_id = _id(data.get("id"), "export chat id")
    chat_id = -(raw_id + _SUPERGROUP_OFFSET if chat_type in _SUPERGROUP_TYPES else raw_id)
    if chat_id < -_MAX_ID:
        raise HistoryExportError("export chat id is outside the supported range")
    if expected_chat_id is not None:
        if type(expected_chat_id) is not int or not -_MAX_ID <= expected_chat_id < 0:
            raise HistoryExportError("expected_chat_id must be a negative group identifier")
        if chat_id != expected_chat_id:
            raise HistoryExportError("export belongs to a different chat")
    if confirmed_thread_id is not None:
        _id(confirmed_thread_id, "confirmed_thread_id")
        if chat_type == "private_group" and confirmed_thread_id != 1:
            raise HistoryExportError("ordinary group must use its canonical root")
    source_messages = data.get("messages")
    if not isinstance(source_messages, list) or len(source_messages) > _MAX_MESSAGES:
        raise HistoryExportError("messages must be a list within the record-count bound")

    index: dict[int, dict[str, Any]] = {}
    duplicates = 0
    for item in source_messages:
        if not isinstance(item, dict):
            raise HistoryExportError("message must be an object")
        message_id = _id(item.get("id"), "message id")
        if message_id in index:
            if _canonical(item) != _canonical(index[message_id]):
                raise HistoryExportError("conflicting duplicate message identifiers")
            duplicates += 1
            continue
        if "reply_to_message_id" in item:
            if _id(item["reply_to_message_id"], "reply_to_message_id") == message_id:
                raise HistoryExportError("message cannot reply to itself")
        index[message_id] = item

    topics = _threads(index, confirmed_thread_id, ordinary_group=chat_type == "private_group")
    skipped: Counter[str] = Counter()
    warnings: Counter[str] = Counter()
    result: list[HistoricalMessage] = []
    for message_id, item in index.items():
        if item.get("type") == "service":
            skipped["service"] += 1
            continue
        if item.get("type") != "message":
            skipped["unsupported_event"] += 1
            continue
        thread = topics.get(message_id)
        if thread is None:
            skipped["unresolved_topic"] += 1
            continue
        author = item.get("from_id")
        if not isinstance(author, str) or not re.fullmatch(r"user[0-9]{1,19}", author):
            skipped["unidentified_author"] += 1
            continue
        author_id = _id(int(author[4:]), "author id")
        if "is_bot" in item and type(item["is_bot"]) is not bool:
            raise HistoryExportError("is_bot must be a bool when present")
        if item.get("is_bot") is True:
            skipped["bot_author"] += 1
            continue
        occurred = _utc(item.get("date_unixtime"), "date_unixtime")
        edited = "edited_unixtime" in item
        if "edited" in item and not edited:
            raise HistoryExportError("edited snapshot lacks edited_unixtime")
        revision = _utc(item["edited_unixtime"], "edited_unixtime") if edited else occurred
        kind, text, coverage, external, unsupported = _body(item)
        if unsupported:
            warnings["unsupported_text_nodes"] += unsupported
        if coverage == "unavailable":
            warnings["body_unavailable"] += 1
        result.append(HistoricalMessage(
            chat_id=chat_id, message_thread_id=thread, telegram_message_id=message_id,
            author_user_id=author_id, occurred_at=occurred, revision_at=revision,
            is_edited=edited, reply_to_message_id=item.get("reply_to_message_id"),
            content_kind=kind, available_text=text, body_coverage=coverage,
            media_reference={}, contains_external_content=external,
        ))
    result.sort(key=lambda item: (item.occurred_at, item.telegram_message_id))
    return TelegramHistoryExport(
        chat_id=chat_id, export_sha256=digest, messages=tuple(result),
        input_messages=len(source_messages), duplicate_messages=duplicates,
        skipped_counts=dict(skipped), warning_counts=dict(warnings),
    )
