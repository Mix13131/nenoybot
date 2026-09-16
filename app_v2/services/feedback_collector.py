from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app_v2.domain.enums import EventType
from app_v2.domain.events import EventEnvelope
from app_v2.domain.feedback import FeedbackEvent
from app_v2.repositories.feedback_repo import FeedbackRecord
from app_v2.services.group_initiative import GroupInitiativeService, is_addressed_to_bot


_REACTION_FAMILIES: dict[str, set[str]] = {
    "laughter_entertainment": {"😁", "😆", "😂", "🤣", "😅", "😄", "😹"},
    "affection": {"❤️", "❤", "❤‍🔥", "🥰", "😍", "🤩", "💘", "😘", "💕", "💖"},
    "approval_support": {"👍", "👏", "👌", "🤝", "💯", "🏆", "🙌", "🙏"},
    "celebration_excitement": {"🔥", "🎉", "🍾", "⚡", "🎊", "🥳"},
    "curiosity_surprise": {"🤔", "🤯", "😱", "👀", "🧐", "😮"},
    "explicit_negative": {"👎", "🤬", "🤮", "😡", "🖕", "😠"},
    "sadness_empathy": {"😢", "😭", "😥", "😔", "🥺"},
    "ambiguous_playful": {"🤡", "💩", "🌚", "😈", "💅", "🗿", "🤷", "🙃", "😏"},
}
_POSITIVE_REACTION_FAMILIES = {
    "laughter_entertainment",
    "affection",
    "approval_support",
    "celebration_excitement",
}
_NEGATIVE_REACTION_FAMILIES = {"explicit_negative"}

_NEGATIVE_PHRASES = (
    "не смешно",
    "не лезь",
    "достал",
    "хуйня",
    "говно",
    "плохой бот",
    "неуместно",
    "перебор",
)


@dataclass(frozen=True)
class FeedbackCollectionResult:
    feedback: FeedbackEvent | None
    record: FeedbackRecord | None


def _reaction_emojis(value: Any) -> list[str]:
    result: list[str] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict) and item.get("type") == "emoji" and item.get("emoji"):
            result.append(str(item["emoji"]))
    return result


def _reaction_identity(item: Any) -> tuple[str, str]:
    if not isinstance(item, dict):
        return "type:unknown", "custom_unknown"

    reaction_type = str(item.get("type") or "unknown")
    if reaction_type == "emoji" and item.get("emoji"):
        emoji = str(item["emoji"])
        for family, members in _REACTION_FAMILIES.items():
            if emoji in members:
                return f"emoji:{emoji}", family
        return f"emoji:{emoji}", "custom_unknown"

    if reaction_type == "custom_emoji" and item.get("custom_emoji_id") is not None:
        return f"custom:{item['custom_emoji_id']}", "custom_unknown"

    return f"type:{reaction_type}", "custom_unknown"


def _unique_in_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _reaction_type(event: EventEnvelope) -> tuple[str, float, dict[str, Any]]:
    old_items = event.metadata.get("old_reaction") or []
    new_items = event.metadata.get("new_reaction") or []
    old_emoji = _reaction_emojis(old_items)
    new_emoji = _reaction_emojis(new_items)

    normalized: list[dict[str, str]] = []
    identities: list[str] = []
    families: list[str] = []
    for item in new_items if isinstance(new_items, list) else []:
        identity, family = _reaction_identity(item)
        normalized.append({"identity": identity, "family": family})
        identities.append(identity)
        families.append(family)

    identities = _unique_in_order(identities)
    families = _unique_in_order(families)
    has_positive = any(family in _POSITIVE_REACTION_FAMILIES for family in families)
    has_negative = any(family in _NEGATIVE_REACTION_FAMILIES for family in families)

    if has_positive and has_negative:
        quality_valence = "mixed_unknown"
    elif has_positive:
        quality_valence = "positive"
    elif has_negative:
        quality_valence = "negative"
    else:
        quality_valence = "unknown"

    payload = {
        "old_reaction": old_items,
        "new_reaction": new_items,
        "old_emoji": old_emoji,
        "new_emoji": new_emoji,
        "reaction_items": normalized,
        "reaction_identities": identities,
        "reaction_families": families,
        "quality_valence": quality_valence,
        "reaction_engaged": bool(normalized),
    }

    if event.event_type is EventType.REACTION_REMOVED or not normalized:
        payload["quality_valence"] = "unknown"
        payload["reaction_engaged"] = False
        return "reaction_removed", 0.0, payload
    if quality_valence == "positive":
        return "reaction_positive", 1.0, payload
    if quality_valence == "negative":
        return "reaction_negative", -1.0, payload
    return "reaction_neutral", 0.0, payload


def _text_feedback_type(
    event: EventEnvelope,
    *,
    resolved_reply: bool,
) -> tuple[str | None, float | None]:
    text = (event.text or "").strip().lower()
    if not is_addressed_to_bot(event):
        return None, None
    if event.event_type is EventType.MUTE_REQUEST:
        return "mute", -1.0
    if event.event_type is EventType.NEGATIVE_FEEDBACK:
        return "explicit_negative", -1.0
    if not text:
        return None, None
    if GroupInitiativeService.is_silence_request(text):
        return "mute", -1.0

    negative = any(phrase in text for phrase in _NEGATIVE_PHRASES)
    if (
        negative
        and event.event_type in {EventType.REPLY_TO_BOT, EventType.REPLY_TO_BOT_MESSAGE}
        and not resolved_reply
    ):
        return "reply_to_bot", 0.0
    if negative:
        return "explicit_negative", -1.0
    if event.event_type in {EventType.REPLY_TO_BOT, EventType.REPLY_TO_BOT_MESSAGE}:
        return "reply_to_bot", 0.0
    if event.event_type is EventType.DIRECT_MENTION:
        return "organic_remention", 0.0
    return None, None


class FeedbackCollector:
    def __init__(self, repo: Any) -> None:
        self.repo = repo

    def collect(self, event: EventEnvelope) -> FeedbackCollectionResult:
        feedback_type: str | None = None
        value: float | None = None
        extra_payload: dict[str, Any] = {}
        intervention_id: int | None = None

        if event.event_type in {EventType.REACTION_ADDED, EventType.REACTION_REMOVED}:
            if event.message_id is not None:
                intervention_id = self.repo.find_intervention_by_bot_message(
                    event.scope_id,
                    event.message_id,
                )
            if intervention_id is None:
                return FeedbackCollectionResult(None, None)
            feedback_type, value, extra_payload = _reaction_type(event)
        else:
            if (
                event.event_type in {EventType.REPLY_TO_BOT, EventType.REPLY_TO_BOT_MESSAGE}
                and event.reply_to_message_id is not None
            ):
                intervention_id = self.repo.find_intervention_by_bot_message(
                    event.scope_id,
                    event.reply_to_message_id,
                )

            feedback_type, value = _text_feedback_type(
                event,
                resolved_reply=intervention_id is not None,
            )
            if feedback_type is None:
                return FeedbackCollectionResult(None, None)

        feedback_id = f"feedback:{event.event_id}:{feedback_type}"
        payload = {
            "source_event_id": event.event_id,
            "source_event_type": event.event_type.value,
            "message_id": event.message_id,
            "reply_to_message_id": event.reply_to_message_id,
            "text": event.text,
            "telegram_metadata": event.metadata,
            "resolved_intervention": intervention_id is not None,
            **extra_payload,
        }
        feedback = FeedbackEvent(
            feedback_id=feedback_id,
            scope_type=event.scope_type,
            scope_id=event.scope_id,
            user_id=event.actor_user_id,
            intervention_id=str(intervention_id) if intervention_id is not None else None,
            feedback_type=feedback_type,
            value=value,
            occurred_at=event.occurred_at,
            payload=payload,
        )
        record = self.repo.record(feedback, intervention_id=intervention_id)
        return FeedbackCollectionResult(feedback, record)
