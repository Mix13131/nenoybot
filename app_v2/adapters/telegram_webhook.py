from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.events import EventEnvelope


@dataclass(frozen=True)
class NormalizedTelegramUpdate:
    telegram_update_id: int
    envelope: EventEnvelope
    telegram_user: dict[str, Any] | None
    telegram_chat: dict[str, Any]
    message: dict[str, Any] | None

    @property
    def is_group(self) -> bool:
        return self.envelope.scope_type is ScopeType.GROUP


def _utc_from_epoch(value: int | float | None) -> datetime:
    timestamp = 0 if value is None else value
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _scope_for_chat(chat: dict[str, Any]) -> ScopeType:
    return ScopeType.PERSONAL if chat.get("type") == "private" else ScopeType.GROUP


def _mention_metadata(message: dict[str, Any]) -> list[dict[str, Any]]:
    mentions: list[dict[str, Any]] = []
    for entity in message.get("entities") or []:
        if entity.get("type") not in {"mention", "text_mention"}:
            continue
        item = {
            "type": entity.get("type"),
            "offset": entity.get("offset"),
            "length": entity.get("length"),
        }
        if entity.get("user") and entity["user"].get("id") is not None:
            item["user_id"] = str(entity["user"]["id"])
        mentions.append(item)
    return mentions


def _normalize_message(update_id: int, message: dict[str, Any], *, edited: bool) -> NormalizedTelegramUpdate | None:
    chat = message.get("chat")
    if not isinstance(chat, dict) or chat.get("id") is None:
        return None

    actor = message.get("from") if isinstance(message.get("from"), dict) else None
    scope_type = _scope_for_chat(chat)
    text = message.get("text") if isinstance(message.get("text"), str) else None
    reply = message.get("reply_to_message") if isinstance(message.get("reply_to_message"), dict) else None
    reply_from = reply.get("from") if reply and isinstance(reply.get("from"), dict) else None
    reply_to_bot = bool(reply_from and reply_from.get("is_bot"))

    if edited:
        event_type = EventType.EDITED_MESSAGE
    elif reply_to_bot:
        event_type = EventType.REPLY_TO_BOT
    elif text and text.lstrip().startswith("/"):
        event_type = EventType.COMMAND
    elif scope_type is ScopeType.PERSONAL:
        event_type = EventType.PRIVATE_MESSAGE
    else:
        event_type = EventType.GROUP_MESSAGE

    message_id = message.get("message_id")
    envelope = EventEnvelope(
        event_id=f"tg:{update_id}",
        event_type=event_type,
        occurred_at=_utc_from_epoch(message.get("date") or message.get("edit_date")),
        scope_type=scope_type,
        scope_id=str(chat["id"]),
        actor_user_id=str(actor["id"]) if actor and actor.get("id") is not None else None,
        message_id=str(message_id) if message_id is not None else None,
        reply_to_message_id=(
            str(reply["message_id"]) if reply and reply.get("message_id") is not None else None
        ),
        text=text,
        metadata={
            "telegram_chat_type": chat.get("type"),
            "reply_to_bot": reply_to_bot,
            "mentions": _mention_metadata(message),
            "edited": edited,
        },
    )
    return NormalizedTelegramUpdate(
        telegram_update_id=update_id,
        envelope=envelope,
        telegram_user=actor,
        telegram_chat=chat,
        message=message,
    )


def _normalize_reaction(update_id: int, reaction: dict[str, Any]) -> NormalizedTelegramUpdate | None:
    chat = reaction.get("chat")
    if not isinstance(chat, dict) or chat.get("id") is None:
        return None
    actor = reaction.get("user") if isinstance(reaction.get("user"), dict) else None
    new_reaction = reaction.get("new_reaction") or []
    event_type = EventType.REACTION_ADDED if new_reaction else EventType.REACTION_REMOVED
    message_id = reaction.get("message_id")
    envelope = EventEnvelope(
        event_id=f"tg:{update_id}",
        event_type=event_type,
        occurred_at=_utc_from_epoch(reaction.get("date")),
        scope_type=_scope_for_chat(chat),
        scope_id=str(chat["id"]),
        actor_user_id=str(actor["id"]) if actor and actor.get("id") is not None else None,
        message_id=str(message_id) if message_id is not None else None,
        metadata={
            "telegram_chat_type": chat.get("type"),
            "old_reaction": reaction.get("old_reaction") or [],
            "new_reaction": new_reaction,
        },
    )
    return NormalizedTelegramUpdate(
        telegram_update_id=update_id,
        envelope=envelope,
        telegram_user=actor,
        telegram_chat=chat,
        message=None,
    )


def normalize_update(update: dict[str, Any]) -> NormalizedTelegramUpdate | None:
    update_id = update.get("update_id")
    if not isinstance(update_id, int):
        return None

    if isinstance(update.get("edited_message"), dict):
        return _normalize_message(update_id, update["edited_message"], edited=True)
    if isinstance(update.get("message"), dict):
        return _normalize_message(update_id, update["message"], edited=False)
    if isinstance(update.get("message_reaction"), dict):
        return _normalize_reaction(update_id, update["message_reaction"])
    return None
