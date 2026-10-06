from __future__ import annotations

import re
from dataclasses import dataclass

from app_v2.domain.enums import EventType, PrimaryAction
from app_v2.domain.events import EventEnvelope, SceneAnalysis


@dataclass(frozen=True)
class ReactionChoice:
    emoji: str
    reason: str


_SPACE_RE = re.compile(r"\s+")
_LAUGHTER = frozenset({"ахаха", "ахах", "хаха", "ха-ха", "лол"})
_APPROVAL = frozenset({"спасибо", "спс", "круто", "супер", "согласен", "точно"})
_FIRE = frozenset({"огонь", "жара"})
_SCHEDULED_TYPES = frozenset(
    {
        EventType.REMINDER_DUE,
        EventType.COMMITMENT_DUE,
        EventType.FOLLOWUP_DUE,
        EventType.SCHEDULED_SUPPORT_MESSAGE,
        EventType.BIRTHDAY_DUE,
        EventType.GROUP_SILENCE_WAKEUP,
    }
)
_EXPLICIT_REACTION_EVENTS = frozenset(
    {
        EventType.DIRECT_MENTION,
        EventType.REPLY_TO_BOT,
        EventType.REPLY_TO_BOT_MESSAGE,
    }
)


def choose_reaction(
    event: EventEnvelope,
    scene: SceneAnalysis,
    *,
    proposed_action: PrimaryAction,
    social_repair: bool,
    has_operational_action: bool,
) -> ReactionChoice | None:
    """Select a bounded acknowledgement without invoking the response generator."""
    if proposed_action is not PrimaryAction.REPLY or event.event_type in _SCHEDULED_TYPES:
        return None
    # Packet 46B is intentionally conservative: reactions are a compact
    # language for turns explicitly addressed to НеНой, not a new autonomous
    # intervention channel. This also keeps existing unsolicited cooldown/share
    # accounting authoritative without adding conversation-ownership inference.
    if event.event_type not in _EXPLICIT_REACTION_EVENTS:
        return None
    if event.event_type in {EventType.COMMAND, EventType.REACTION_ADDED, EventType.REACTION_REMOVED}:
        return None
    if not event.message_id or not event.message_id.isdigit():
        return None
    if social_repair or has_operational_action:
        return None
    if any((
        scene.question_to_bot,
        bool(scene.command_intent),
        scene.help_opportunity >= 0.25,
        scene.seriousness_score >= 0.35,
        scene.sensitivity_score >= 0.25,
        scene.conflict_score >= 0.25,
        scene.memory_value >= 0.25,
        scene.commitment_signal >= 0.25,
        scene.decision_signal >= 0.25,
    )):
        return None

    normalized = _SPACE_RE.sub(" ", (event.text or "").strip().lower()).strip(".!…")
    if normalized in _LAUGHTER:
        return ReactionChoice("😂", "short_laughter_acknowledgement")
    if normalized in _APPROVAL:
        return ReactionChoice("👍", "short_positive_acknowledgement")
    if normalized in _FIRE:
        return ReactionChoice("🔥", "short_enthusiastic_acknowledgement")
    return None

