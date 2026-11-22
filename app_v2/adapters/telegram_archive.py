"""Conservative Telegram -> source-archive projection, without model calls.

Only explicitly identified forum-topic messages by a real individual are
supported. Source fields: https://core.telegram.org/bots/api#message.
Neither an embedded reply nor a forward's original author becomes an archived
author. Manually typed quotations cannot be reliably detected by this adapter;
Stage A provides no semantic attribution or analysis of them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app_v2.adapters.telegram_webhook import NormalizedTelegramUpdate
from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.source_archive import ArchiveMessage


_MAX_ID = (1 << 63) - 1
_FILE_KINDS = ("animation", "audio", "document", "video", "voice", "video_note", "sticker")
_OTHER_KINDS = (
    "live_photo", "paid_media", "contact", "location", "venue", "poll", "dice",
    "game", "story", "rich_message", "widget",
)
_EXTERNAL_FIELDS = frozenset({
    "forward_origin", "forward_from", "forward_from_chat", "forward_from_message_id",
    "forward_signature", "forward_sender_name", "forward_date", "quote", "external_reply",
})


def _valid_id(value: object, *, minimum: int = 1) -> bool:
    return type(value) is int and minimum <= value <= _MAX_ID


def _timestamp(value: object) -> datetime | None:
    if not _valid_id(value):
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _media(message: dict[str, Any]) -> tuple[str | None, dict[str, str | int]]:
    kind: str | None = None
    data: dict[str, Any] = {}
    if "photo" in message:
        kind = "photo"
        photos = message.get("photo")
        # The final variant is normally largest; no image bytes are retrieved.
        if isinstance(photos, list):
            data = next((item for item in reversed(photos) if isinstance(item, dict)), {})
    else:
        kind = next((key for key in _FILE_KINDS if key in message), None)
        if kind is not None and isinstance(message[kind], dict):
            data = message[kind]
    if kind is None:
        kind = next((key for key in _OTHER_KINDS if key in message), None)

    reference: dict[str, str | int] = {}
    for key in ("file_id", "file_unique_id"):
        value = data.get(key)
        if isinstance(value, str) and value and len(value) <= 2048:
            reference[key] = value
    if _valid_id(data.get("file_size"), minimum=0):
        reference["file_size"] = data["file_size"]
    group_id = message.get("media_group_id")
    if kind is not None and isinstance(group_id, str) and group_id and len(group_id) <= 2048:
        reference["media_group_id"] = group_id
    return kind, reference


def _has_external_content(message: dict[str, Any]) -> bool:
    if _EXTERNAL_FIELDS.intersection(message) or message.get("is_automatic_forward") is True:
        return True
    for key in ("entities", "caption_entities"):
        entities = message.get(key)
        if isinstance(entities, list) and any(
            isinstance(item, dict) and item.get("type") in {"blockquote", "expandable_blockquote"}
            for item in entities
        ):
            return True
    return False


def archive_message_from_update(normalized: NormalizedTelegramUpdate) -> ArchiveMessage | None:
    """Project a supported received source, or fail closed on uncertain identity.

    This is *not* a consent check. The caller must atomically enforce the current
    author/topic retention grant before persisting the returned record.
    """

    message = normalized.message
    if not isinstance(message, dict) or normalized.envelope.scope_type is not ScopeType.GROUP:
        return None
    chat = message.get("chat")
    author = message.get("from")
    if not isinstance(chat, dict) or not isinstance(author, dict):
        return None
    if chat.get("type") != "supergroup" or chat.get("is_forum") is not True:
        return None
    if message.get("is_topic_message") is not True:
        return None
    # sender_chat's fake `from` never identifies the anonymous human author.
    if "sender_chat" in message or author.get("is_bot") is not False:
        return None
    # Ephemeral group messages have a limited audience, not ordinary group scope.
    if any(key in message for key in ("ephemeral_message_id", "receiver_user", "sender_business_bot")):
        return None
    chat_id, author_id = chat.get("id"), author.get("id")
    if type(chat_id) is not int or not -_MAX_ID <= chat_id < 0:
        return None
    if not all(_valid_id(item) for item in (
        author_id, message.get("message_thread_id"), message.get("message_id"),
    )) or not _valid_id(normalized.telegram_update_id, minimum=0):
        return None
    # Normalized routing fields must agree with the original source projection.
    if normalized.telegram_chat.get("id") != chat_id:
        return None
    if not normalized.telegram_user or normalized.telegram_user.get("id") != author_id:
        return None
    if (
        normalized.envelope.scope_id != str(chat_id)
        or normalized.envelope.actor_user_id != str(author_id)
        or normalized.envelope.message_id != str(message["message_id"])
    ):
        return None

    occurred_at = _timestamp(message.get("date"))
    edited = normalized.envelope.event_type is EventType.EDITED_MESSAGE
    if "edit_date" in message:
        revision_at = _timestamp(message["edit_date"])
        edited = True
    elif edited:
        return None
    else:
        revision_at = occurred_at
    if occurred_at is None or revision_at is None or revision_at < occurred_at:
        return None

    reply_to = None
    reply = message.get("reply_to_message")
    if reply is not None:
        if not isinstance(reply, dict) or not _valid_id(reply.get("message_id")):
            return None
        reply_to = reply["message_id"]

    for key in ("text", "caption"):
        if key in message and message[key] is not None and not isinstance(message[key], str):
            return None
    text = message.get("text")
    caption = message.get("caption")
    if text is not None and caption is not None:
        return None  # Two competing text bodies would silently discard one.
    available_text = text if text is not None else caption
    media_kind, reference = _media(message)
    content_kind = media_kind or ("text" if text is not None else "caption" if caption is not None else "unsupported")
    coverage = "unavailable" if available_text is None else "full" if content_kind == "text" else "partial"
    try:
        return ArchiveMessage(
            chat_id=chat_id,
            message_thread_id=message["message_thread_id"],
            telegram_message_id=message["message_id"],
            telegram_update_id=normalized.telegram_update_id,
            author_user_id=author_id,
            occurred_at=occurred_at,
            revision_at=revision_at,
            is_edited=edited,
            reply_to_message_id=reply_to,
            content_kind=content_kind,
            available_text=available_text,
            body_coverage=coverage,
            media_reference=reference,
            contains_external_content=_has_external_content(message),
        )
    except (ValueError, TypeError):
        return None
