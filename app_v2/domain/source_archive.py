"""Immutable received-source records, with no analysis or transport dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Literal


BodyCoverage = Literal["full", "partial", "unavailable"]
_MAX_ID = (1 << 63) - 1
_CONTENT_KINDS = frozenset({
    "text", "caption", "photo", "video", "animation", "audio", "voice",
    "document", "sticker", "video_note", "live_photo", "paid_media",
    "contact", "location", "venue", "poll", "dice", "game", "story",
    "rich_message", "widget", "unsupported",
})
_REFERENCE_KEYS = frozenset({"file_id", "file_unique_id", "file_size", "media_group_id"})


class _FrozenReference(dict):
    """A JSON-serializable dict that cannot invalidate its owner's checksum."""

    def _immutable(self, *args, **kwargs):
        raise TypeError("media_reference is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


def _integer(value: object, name: str, *, minimum: int = 1) -> None:
    if type(value) is not int or not minimum <= value <= _MAX_ID:
        raise ValueError(f"{name} must be an integer in [{minimum}, {_MAX_ID}]")


@dataclass(frozen=True)
class ArchiveMessage:
    """A revision of one received message; permissions belong to the repository.

    ``full`` describes captured *text*, never downloaded media. An external-content
    flag prevents use as first-person evidence even when the text was captured in
    full. Nested reply/forward/quote payloads are never part of this record.
    """

    chat_id: int
    message_thread_id: int
    telegram_message_id: int
    telegram_update_id: int
    author_user_id: int
    occurred_at: datetime
    revision_at: datetime
    is_edited: bool
    reply_to_message_id: int | None
    content_kind: str
    available_text: str | None
    body_coverage: BodyCoverage
    media_reference: dict[str, str | int]
    contains_external_content: bool

    def __post_init__(self) -> None:
        if type(self.chat_id) is not int or not -_MAX_ID <= self.chat_id < 0:
            raise ValueError("chat_id must be a negative Telegram group integer")
        for name in ("message_thread_id", "telegram_message_id", "author_user_id"):
            _integer(getattr(self, name), name)
        _integer(self.telegram_update_id, "telegram_update_id", minimum=0)
        if self.reply_to_message_id is not None:
            _integer(self.reply_to_message_id, "reply_to_message_id")
        for name in ("is_edited", "contains_external_content"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a bool")
        for name in ("occurred_at", "revision_at"):
            value = getattr(self, name)
            if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
                raise ValueError(f"{name} must be a UTC timezone-aware datetime")
            object.__setattr__(self, name, value.astimezone(timezone.utc))
        if self.occurred_at <= datetime(1970, 1, 1, tzinfo=timezone.utc):
            raise ValueError("occurred_at must be after the Unix epoch")
        if self.revision_at < self.occurred_at:
            raise ValueError("revision_at cannot precede occurred_at")
        if not self.is_edited and self.revision_at != self.occurred_at:
            raise ValueError("an unedited source has one timestamp")
        if not isinstance(self.content_kind, str) or self.content_kind not in _CONTENT_KINDS:
            raise ValueError("unsupported content_kind")
        if self.available_text is not None and not isinstance(self.available_text, str):
            raise ValueError("available_text must be a string or None")
        if self.body_coverage not in {"full", "partial", "unavailable"}:
            raise ValueError("invalid body_coverage")
        if self.body_coverage == "full" and (
            self.content_kind != "text" or self.available_text is None
        ):
            raise ValueError("full coverage requires captured plain text")
        if self.body_coverage == "partial" and self.available_text is None:
            raise ValueError("partial coverage requires captured text or caption")
        if self.body_coverage == "unavailable" and self.available_text is not None:
            raise ValueError("unavailable coverage cannot include captured text")
        if not isinstance(self.media_reference, dict):
            raise ValueError("media_reference must be a flat dict")
        if not self.media_reference.keys() <= _REFERENCE_KEYS:
            raise ValueError("media_reference contains a non-whitelisted key")
        for key, value in self.media_reference.items():
            if key == "file_size":
                _integer(value, "file_size", minimum=0)
            elif not isinstance(value, str) or not value or len(value) > 2048:
                raise ValueError(f"{key} must be a nonempty reference string")
        if self.content_kind == "text" and self.media_reference:
            raise ValueError("plain text cannot claim media references")
        object.__setattr__(self, "media_reference", _FrozenReference(self.media_reference))

    @property
    def source_id(self) -> str:
        return (
            f"tgsrc:{self.chat_id}:{self.message_thread_id}:"
            f"{self.telegram_message_id}:{self.telegram_update_id}"
        )

    @property
    def checksum(self) -> str:
        payload = {
            "archive_message_version": 1,
            "chat_id": self.chat_id,
            "message_thread_id": self.message_thread_id,
            "telegram_message_id": self.telegram_message_id,
            "telegram_update_id": self.telegram_update_id,
            "author_user_id": self.author_user_id,
            "occurred_at": self.occurred_at.isoformat(timespec="microseconds"),
            "revision_at": self.revision_at.isoformat(timespec="microseconds"),
            "is_edited": self.is_edited,
            "reply_to_message_id": self.reply_to_message_id,
            "content_kind": self.content_kind,
            "available_text": self.available_text,
            "body_coverage": self.body_coverage,
            "media_reference": self.media_reference,
            "contains_external_content": self.contains_external_content,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
