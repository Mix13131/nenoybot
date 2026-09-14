from __future__ import annotations

from dataclasses import dataclass, field

from app_v2.domain.decisions import DispatcherDecision
from app_v2.domain.enums import (
    EventType,
    PrimaryAction,
    ReasonCode,
    ResponseMode,
    ScopeType,
)
from app_v2.domain.events import EventEnvelope, SceneAnalysis

POLICY_VERSION = "dispatcher-deterministic-v1"


@dataclass(frozen=True)
class DispatcherPolicyState:
    group_muted: bool = False
    cooldown_active: bool = False
    unsolicited_today: int = 0
    soft_daily_limit: int = 6
    hard_daily_limit: int = 10
    initiative_level: int = 6
    bot_spoke_recently: bool = False
    ignored_unsolicited_recent: int = 0
    running_joke_fit: bool = False
    broken_commitment_relevant: bool = False
    conversation_about_bot: bool = False
    allow_roast: bool = True
    allow_callbacks: bool = True
    metadata: dict[str, object] = field(default_factory=dict)


def _is_explicit(event: EventEnvelope, scene: SceneAnalysis) -> tuple[bool, list[ReasonCode]]:
    reasons: list[ReasonCode] = []
    direct = scene.direct_mention or event.event_type is EventType.DIRECT_MENTION
    reply = scene.reply_to_bot or event.event_type in {
        EventType.REPLY_TO_BOT,
        EventType.REPLY_TO_BOT_MESSAGE,
    }
    question = scene.question_to_bot

    if direct:
        reasons.append(ReasonCode.DIRECT_MENTION)
    if reply:
        reasons.append(ReasonCode.REPLY_TO_BOT)
    if question:
        reasons.append(ReasonCode.QUESTION_TO_BOT)
    return bool(reasons), reasons


def _personal_mode(event: EventEnvelope, scene: SceneAnalysis) -> ResponseMode:
    if event.event_type in {
        EventType.REMINDER_DUE,
        EventType.COMMITMENT_DUE,
        EventType.FOLLOWUP_DUE,
    }:
        return ResponseMode.EXECUTION
    if scene.sensitivity_score >= 0.75 or scene.seriousness_score >= 0.85:
        return ResponseMode.CARE
    if scene.contradiction_score >= 0.80:
        return ResponseMode.MIRROR
    if scene.commitment_signal >= 0.75:
        return ResponseMode.COACH
    return ResponseMode.ASSISTANT


def _score_group(
    scene: SceneAnalysis,
    state: DispatcherPolicyState,
) -> tuple[int, list[ReasonCode], list[dict[str, int]]]:
    score = 0
    reasons: list[ReasonCode] = []
    components: list[dict[str, int]] = []

    def add(reason: ReasonCode, weight: int) -> None:
        nonlocal score
        score += weight
        reasons.append(reason)
        components.append({"reason": reason.value, "weight": weight})

    if state.allow_callbacks and scene.callback_opportunity >= 0.75:
        add(ReasonCode.CALLBACK_OPPORTUNITY, 35)
    if scene.contradiction_score >= 0.75:
        add(ReasonCode.CONTRADICTION, 30)
    if state.broken_commitment_relevant:
        add(ReasonCode.BROKEN_COMMITMENT, 25)
    if state.allow_roast and scene.roast_opportunity >= 0.80:
        add(ReasonCode.ROAST_OPPORTUNITY, 25)
    if scene.help_opportunity >= 0.80:
        add(ReasonCode.HELP_OPPORTUNITY, 20)
    if state.running_joke_fit:
        add(ReasonCode.RUNNING_JOKE, 20)
    if state.conversation_about_bot:
        components.append({"reason": "conversation_about_bot", "weight": 15})
        score += 15
    if state.bot_spoke_recently:
        add(ReasonCode.BOT_SPOKE_RECENTLY, -35)
    if state.ignored_unsolicited_recent >= 2:
        add(ReasonCode.PREVIOUS_UNSOLICITED_IGNORED, -35)
    elif state.ignored_unsolicited_recent == 1:
        add(ReasonCode.PREVIOUS_UNSOLICITED_IGNORED, -20)
    if scene.seriousness_score >= 0.75:
        add(ReasonCode.SERIOUS_CONTEXT, -60)
    if scene.sensitivity_score >= 0.75:
        add(ReasonCode.SENSITIVE_CONTEXT, -70)
    if state.unsolicited_today >= state.soft_daily_limit:
        add(ReasonCode.SOFT_DAILY_LIMIT, -20)

    return max(0, min(100, score)), reasons, components


def _group_mode(scene: SceneAnalysis, state: DispatcherPolicyState) -> ResponseMode:
    safe_for_roast = scene.seriousness_score < 0.75 and scene.sensitivity_score < 0.75

    if state.allow_callbacks and scene.callback_opportunity >= 0.80:
        return ResponseMode.GROUP_CALLBACK
    if state.allow_roast and safe_for_roast and scene.roast_opportunity >= 0.80:
        return ResponseMode.GROUP_ROAST
    if scene.help_opportunity >= 0.80:
        return ResponseMode.GROUP_HELP
    return ResponseMode.GROUP_BANTER


def decide(
    event: EventEnvelope,
    scene: SceneAnalysis | None = None,
    state: DispatcherPolicyState | None = None,
) -> DispatcherDecision:
    """Return a deterministic Dispatcher decision without calling an LLM."""
    scene = scene or SceneAnalysis()
    state = state or DispatcherPolicyState()

    if event.scope_type is ScopeType.PERSONAL:
        return DispatcherDecision(
            primary_action=PrimaryAction.REPLY,
            mode=_personal_mode(event, scene),
            intervention_score=100,
            reason_codes=[ReasonCode.PERSONAL_DEFAULT_REPLY],
            target_user_id=event.actor_user_id,
            metadata={
                "policy_version": POLICY_VERSION,
                "unsolicited": False,
                **state.metadata,
            },
        )

    explicit, explicit_reasons = _is_explicit(event, scene)
    if explicit:
        return DispatcherDecision(
            primary_action=PrimaryAction.REPLY,
            mode=ResponseMode.GROUP_DIRECT_REPLY,
            intervention_score=100,
            reason_codes=explicit_reasons,
            target_user_id=event.actor_user_id,
            metadata={
                "policy_version": POLICY_VERSION,
                "unsolicited": False,
                **state.metadata,
            },
        )

    if state.group_muted:
        return DispatcherDecision(
            primary_action=PrimaryAction.IGNORE,
            intervention_score=0,
            reason_codes=[ReasonCode.SILENCE_REQUESTED],
            metadata={"policy_version": POLICY_VERSION, "unsolicited": True, **state.metadata},
        )

    if state.unsolicited_today >= state.hard_daily_limit:
        return DispatcherDecision(
            primary_action=PrimaryAction.IGNORE,
            intervention_score=0,
            reason_codes=[ReasonCode.HARD_DAILY_LIMIT],
            metadata={"policy_version": POLICY_VERSION, "unsolicited": True, **state.metadata},
        )

    if state.cooldown_active:
        return DispatcherDecision(
            primary_action=PrimaryAction.IGNORE,
            intervention_score=0,
            reason_codes=[ReasonCode.COOLDOWN_ACTIVE],
            metadata={"policy_version": POLICY_VERSION, "unsolicited": True, **state.metadata},
        )

    score, reasons, components = _score_group(scene, state)
    should_reply = score >= 75
    if 60 <= score <= 74 and state.initiative_level >= 8:
        should_reply = True
        reasons.append(ReasonCode.HIGH_INITIATIVE)
        components.append({"reason": ReasonCode.HIGH_INITIATIVE.value, "weight": 0})

    if not should_reply:
        if not reasons:
            reasons = [ReasonCode.GROUP_DEFAULT_SILENCE]
        return DispatcherDecision(
            primary_action=PrimaryAction.IGNORE,
            intervention_score=score,
            reason_codes=reasons,
            target_user_id=event.actor_user_id,
            metadata={
                "policy_version": POLICY_VERSION,
                "unsolicited": True,
                "score_components": components,
                **state.metadata,
            },
        )

    return DispatcherDecision(
        primary_action=PrimaryAction.REPLY,
        mode=_group_mode(scene, state),
        intervention_score=score,
        reason_codes=reasons,
        target_user_id=event.actor_user_id,
        metadata={
            "policy_version": POLICY_VERSION,
            "unsolicited": True,
            "score_components": components,
            **state.metadata,
        },
    )
