from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app_v2.domain.enums import EventType, PrimaryAction, ResponseMode, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.services.connector_presets import build_connector_preset
from app_v2.services.context_builder import GenerationContext, estimate_tokens
from app_v2.services.dispatcher import DispatcherPolicyState, decide
from app_v2.services.operation_receipts import group_operation_receipts


LAB_BEHAVIOR_REPLAY_VERSION = 1


@dataclass(frozen=True)
class BehaviorReplayOptions:
    preset_name: str = "education_community_v1"


def _case_datetime(value: Any) -> datetime:
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime(2000, 1, 1, tzinfo=timezone.utc)


def _hot_messages(context: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    result: list[dict[str, Any]] = []
    for item in context:
        text = str(item.get("text") or "").strip()
        if not text and not item.get("media"):
            continue
        result.append(
            {
                "message_id": item.get("id"),
                "author_user_id": item.get("actor"),
                "text": text or "[media]",
                "created_at": item.get("occurred_at"),
                "reply_to_message_id": item.get("reply_to"),
            }
        )
    return tuple(result)


def _hot_token_estimate(messages: Sequence[Mapping[str, Any]]) -> int:
    return sum(estimate_tokens(str(item.get("text") or "")) + 12 for item in messages)


class GroupBehaviorReplayRunner:
    """Counterfactual frozen-turn replay using current v2 Brain components.

    This intentionally does not use production repositories, LONG memory,
    dynamic initiative history, reminders, scheduler or Telegram outbox.
    Each case is evaluated independently against the original frozen human
    history so simulated bot replies never mutate later historical cases.
    """

    def __init__(
        self,
        *,
        scene_analyzer: Any,
        personality_engine: Any,
        response_generator: Any,
        options: BehaviorReplayOptions | None = None,
    ) -> None:
        self.scene_analyzer = scene_analyzer
        self.personality_engine = personality_engine
        self.response_generator = response_generator
        self.options = options or BehaviorReplayOptions()
        self.connector = build_connector_preset(
            self.options.preset_name,
            connector_id=f"lab:{self.options.preset_name}",
        )

    @property
    def parity(self) -> dict[str, Any]:
        return {
            "scene_analyzer": "production_component",
            "dispatcher": "production_component",
            "personality_engine": "production_component",
            "response_generator": "production_component",
            "connector_preset": self.options.preset_name,
            "frozen_turns": True,
            "long_memory": "disabled",
            "callback_retrieval": "disabled",
            "statement_watch": "disabled",
            "dynamic_cooldown_history": "not_replayed",
            "feedback_history": "not_replayed",
            "reminders_and_actions": "disabled",
            "telegram_outbox": "disabled",
        }

    def evaluate_case(self, case: Mapping[str, Any]) -> dict[str, Any]:
        current_raw = case.get("current")
        if not isinstance(current_raw, Mapping):
            raise ValueError("replay case must contain current message")

        case_id = str(case.get("id") or "").strip()
        if not case_id:
            raise ValueError("replay case id is required")

        actor = str(current_raw.get("actor") or "member_unknown")
        event = EventEnvelope(
            event_id=f"lab:{case_id}",
            event_type=EventType.GROUP_MESSAGE,
            occurred_at=_case_datetime(current_raw.get("occurred_at")),
            scope_type=ScopeType.GROUP,
            scope_id="lab_group",
            actor_user_id=actor,
            message_id=str(current_raw.get("id") or case_id),
            reply_to_message_id=(
                str(current_raw.get("reply_to"))
                if current_raw.get("reply_to") is not None
                else None
            ),
            text=str(current_raw.get("text") or "") or None,
            metadata={
                "lab_replay": True,
                "source_episode_id": case.get("episode_id"),
                "direct_mention": False,
                "reply_to_bot": False,
            },
        )

        # Production GroupPipeline calls SceneAnalyzer without recent_context
        # for ordinary GROUP_MESSAGE events. Keep that exact routing behavior.
        scene = self.scene_analyzer.analyze(event)

        profile = self.connector.personality.as_context_profile(
            self.connector.identity.preset
        )
        behavior = self.connector.behavior
        state = DispatcherPolicyState(
            group_muted=False,
            cooldown_active=not behavior.unsolicited_enabled,
            unsolicited_today=0,
            soft_daily_limit=behavior.soft_daily_limit,
            hard_daily_limit=behavior.hard_daily_limit,
            initiative_level=behavior.initiative,
            bot_spoke_recently=False,
            ignored_unsolicited_recent=0,
            running_joke_fit=False,
            broken_commitment_relevant=False,
            priority_statement=False,
            allow_roast=int(profile.get("roast", 0) or 0) > 0,
            allow_callbacks=False,
            metadata={
                "lab_replay": True,
                "connector": self.connector.public_state(),
            },
        )
        decision = decide(event, scene, state)

        output: dict[str, Any] = {
            "case_id": case_id,
            "episode_id": case.get("episode_id"),
            "current_message_id": current_raw.get("id"),
            "scene": scene.model_dump(mode="json"),
            "decision": {
                "primary_action": decision.primary_action.value,
                "mode": decision.mode.value if decision.mode else None,
                "intervention_score": decision.intervention_score,
                "reason_codes": [reason.value for reason in decision.reason_codes],
                "unsolicited": bool(decision.metadata.get("unsolicited")),
            },
            "generated_text": None,
            "model": None,
            "parity": self.parity,
        }

        if decision.primary_action is not PrimaryAction.REPLY:
            return output

        mode = decision.mode or ResponseMode.GROUP_DIRECT_REPLY
        personality = self.personality_engine.build(
            scope_type=ScopeType.GROUP,
            mode=mode,
            scene=scene,
            context_profile=profile,
            participant_adaptation={},
        )

        raw_context = case.get("context")
        context_rows = (
            [item for item in raw_context if isinstance(item, Mapping)]
            if isinstance(raw_context, list)
            else []
        )
        hot = _hot_messages(context_rows)

        generation_context = GenerationContext(
            scope_type=ScopeType.GROUP,
            scope_id="lab_group",
            event=event.model_dump(mode="json"),
            scene=scene.model_dump(mode="json"),
            decision=decision.model_dump(mode="json"),
            personality=personality.model_dump(mode="json"),
            hot_messages=hot,
            memories=(),
            target_user_id=decision.target_user_id,
            action_state={
                "connector": self.connector.public_state(),
                "operation_receipts": group_operation_receipts(
                    None,
                    memory_attempted=False,
                    reminder_action_state=None,
                ),
                "_lab_replay": {
                    "case_id": case_id,
                    "frozen": True,
                    "side_effects": False,
                },
            },
            estimated_hot_tokens=_hot_token_estimate(hot),
            estimated_memory_tokens=0,
        )
        generated = self.response_generator.generate(generation_context)
        output["generated_text"] = generated.text
        output["model"] = generated.model
        return output


def run_behavior_replay(
    replay: Mapping[str, Any],
    *,
    runner: GroupBehaviorReplayRunner,
    case_ids: set[str] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    raw_cases = replay.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError("frozen replay must contain cases list")
    if limit is not None and limit < 1:
        raise ValueError("limit must be >= 1")

    selected: list[Mapping[str, Any]] = []
    for item in raw_cases:
        if not isinstance(item, Mapping):
            continue
        case_id = str(item.get("id") or "")
        if case_ids is not None and case_id not in case_ids:
            continue
        selected.append(item)
        if limit is not None and len(selected) >= limit:
            break

    results = [runner.evaluate_case(case) for case in selected]
    replies = sum(
        1
        for item in results
        if item["decision"]["primary_action"] == PrimaryAction.REPLY.value
    )
    return {
        "lab_behavior_replay_version": LAB_BEHAVIOR_REPLAY_VERSION,
        "source_replay_version": replay.get("lab_replay_version"),
        "preset": runner.options.preset_name,
        "parity": runner.parity,
        "stats": {
            "evaluated_cases": len(results),
            "reply_cases": replies,
            "ignore_cases": len(results) - replies,
        },
        "results": results,
    }
