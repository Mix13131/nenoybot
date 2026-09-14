from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app_v2.domain.enums import EventType
from app_v2.domain.events import EventEnvelope
from app_v2.domain.feedback import FeedbackEvent
from app_v2.repositories.feedback_repo import FeedbackRecord
from app_v2.services.group_initiative import GroupInitiativeService


_POSITIVE_EMOJI = {"👍", "❤", "❤️", "🔥", "👏", "🎉", "😁", "😂", "🤝", "💯"}
_NEGATIVE_EMOJI = {"👎", "💩", "🤡", "🤬", "😡", "🙄"}
_NEGATIVE_PHRASES = (
    "не смешно",
    "не надо",
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


def _reaction_type(event: EventEnvelope) -> tuple[str, float, dict[str, Any]]:
    old_items = event.metadata.get("old_reaction") or []
    new_items = event.metadata.get("new_reaction") or []
    old_emoji = _reaction_emojis(old_items)
    new_emoji = _reaction_emojis(new_items)
    payload = {
        "old_reaction": old_items,
        "new_reaction": new_items,
        "old_emoji": old_emoji,
        "new_emoji": new_emoji,
    }
    if event.event_type is EventType.REACTION_REMOVED or not new_emoji:
        return "reaction_removed", 0.0, payload
    if any(item in _NEGATIVE_EMOJI for item in new_emoji):
        return "reaction_negative", -1.0, payload
    if any(item in _POSITIVE_EMOJI for item in new_emoji):
        return "reaction_positive", 1.0, payload
    return "reaction_neutral", 0.0, payload


def _text_feedback_type(event: EventEnvelope) -> tuple[str | None, float | None]:
    text = (event.text or "").strip().lower()
    if not text:
        return None, None
    if GroupInitiativeService.is_silence_request(text):
        return "mute", -1.0
    if any(phrase in text for phrase in _NEGATIVE_PHRASES):
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
            feedback_type, value, extra_payload = _reaction_type(event)
            if event.message_id is not None:
                intervention_id = self.repo.find_intervention_by_bot_message(
                    event.scope_id,
                    event.message_id,
                )
        else:
            feedback_type, value = _text_feedback_type(event)
            if feedback_type is None:
                return FeedbackCollectionResult(None, None)
            if event.reply_to_message_id is not None:
                intervention_id = self.repo.find_intervention_by_bot_message(
                    event.scope_id,
                    event.reply_to_message_id,
                )
            if intervention_id is None:
                intervention_id = self.repo.latest_intervention(
                    event.scope_id,
                    before=event.occurred_at,
                )

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
