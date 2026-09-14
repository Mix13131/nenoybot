from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app_v2.domain.enums import PrimaryAction, ResponseMode, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.domain.outbound import OutboundMessage
from app_v2.services.dispatcher import DispatcherPolicyState, decide


class GroupPipelineError(RuntimeError):
    pass


@dataclass(frozen=True)
class GroupPipelineResult:
    event_id: str
    allowed: bool
    access_reason: str
    primary_action: PrimaryAction | None = None
    mode: ResponseMode | None = None
    generated_text: str | None = None
    outbox_id: int | None = None
    outbox_created: bool = False
    selected_memory_ids: tuple[str, ...] = ()
    generation_failed: bool = False


class GroupPipeline:
    """Group orchestration for approved test groups.

    With no GroupBehaviorEngine attached, TASK 18 silence-first behavior remains
    the default. TASK 19 can attach a behavior engine to enable evidence-backed
    callbacks/roasts under group feature flags and participant gates.
    """

    def __init__(
        self,
        *,
        access_service: Any,
        scene_analyzer: Any,
        personality_engine: Any,
        context_builder: Any,
        response_generator: Any,
        intervention_repo: Any,
        outbox_repo: Any,
        group_behavior_engine: Any | None = None,
        unsolicited_enabled: bool = False,
    ) -> None:
        self.access_service = access_service
        self.scene_analyzer = scene_analyzer
        self.personality_engine = personality_engine
        self.context_builder = context_builder
        self.response_generator = response_generator
        self.intervention_repo = intervention_repo
        self.outbox_repo = outbox_repo
        self.group_behavior_engine = group_behavior_engine
        self.unsolicited_enabled = unsolicited_enabled

    def process(self, event: EventEnvelope, *, now: datetime | None = None) -> GroupPipelineResult:
        if event.scope_type is not ScopeType.GROUP:
            raise GroupPipelineError("GroupPipeline accepts only group scope events")

        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now must be timezone-aware")

        access = self.access_service.evaluate(event, now=current)
        if not access.allowed or access.context is None:
            return GroupPipelineResult(
                event_id=event.event_id,
                allowed=False,
                access_reason=access.reason,
            )

        group_context = access.context
        scene = self.scene_analyzer.analyze(event)

        profile = dict(group_context.profile or {})
        participant_profile = dict(group_context.participant.profile or {})
        adaptation = participant_profile.get("personality_modifiers")
        if not isinstance(adaptation, dict):
            adaptation = {}
        memory_usage = "assist"
        callback_fatigue_minutes = 60
        behavior_memory_ids: tuple[str, ...] = ()

        if self.group_behavior_engine is not None:
            plan = self.group_behavior_engine.plan(
                event=event,
                group_context=group_context,
                scene=scene,
                now=current,
            )
            scene = plan.scene
            state = plan.state
            profile = dict(plan.context_profile)
            adaptation = dict(plan.participant_adaptation)
            memory_usage = plan.memory_usage
            callback_fatigue_minutes = plan.callback_fatigue_minutes
            behavior_memory_ids = tuple(plan.callback_memory_ids)
        else:
            muted = bool(group_context.silent_until and group_context.silent_until > current)
            state = DispatcherPolicyState(
                group_muted=muted,
                cooldown_active=(not self.unsolicited_enabled),
                initiative_level=int(profile.get("initiative", 6) or 6),
                allow_roast=False,
                allow_callbacks=False,
                metadata={"group_profile": profile.get("profile", "friends")},
            )

        decision = decide(event, scene, state)

        if decision.primary_action is not PrimaryAction.REPLY:
            self.intervention_repo.record(
                event_id=event.event_id,
                scope_type=event.scope_type,
                scope_id=event.scope_id,
                decision=decision,
                selected_memory_ids=list(behavior_memory_ids),
                generated_text=None,
                extra_metadata={
                    "access_reason": access.reason,
                    "group_behavior_probe_ids": list(behavior_memory_ids),
                },
            )
            return GroupPipelineResult(
                event_id=event.event_id,
                allowed=True,
                access_reason=access.reason,
                primary_action=decision.primary_action,
                mode=decision.mode,
                selected_memory_ids=behavior_memory_ids,
            )

        personality = self.personality_engine.build(
            scope_type=ScopeType.GROUP,
            mode=decision.mode or ResponseMode.GROUP_DIRECT_REPLY,
            scene=scene,
            context_profile=profile,
            participant_adaptation=adaptation,
        )
        subject_keys = [f"user:{event.actor_user_id}"] if event.actor_user_id else []
        context = self.context_builder.build(
            event=event,
            scene=scene,
            decision=decision,
            personality=personality,
            subject_keys=subject_keys,
            memory_usage=memory_usage,
            callback_fatigue_minutes=callback_fatigue_minutes,
            action_state={
                "group_title": group_context.title,
                "participant_role": group_context.participant.role,
                "behavior_probe_ids": list(behavior_memory_ids),
            },
        )
        if context.scope_type is not ScopeType.GROUP or context.scope_id != event.scope_id:
            raise GroupPipelineError("Context Builder returned cross-scope Group context")

        selected_memory_ids = tuple(memory.id for memory in context.memories)
        try:
            generated = self.response_generator.generate(context)
        except Exception as exc:
            self.intervention_repo.record(
                event_id=event.event_id,
                scope_type=event.scope_type,
                scope_id=event.scope_id,
                decision=decision,
                selected_memory_ids=list(selected_memory_ids),
                generated_text=None,
                extra_metadata={
                    "generation_failed": True,
                    "error_type": type(exc).__name__,
                    "access_reason": access.reason,
                    "group_behavior_probe_ids": list(behavior_memory_ids),
                },
            )
            return GroupPipelineResult(
                event_id=event.event_id,
                allowed=True,
                access_reason=access.reason,
                primary_action=decision.primary_action,
                mode=decision.mode,
                selected_memory_ids=selected_memory_ids,
                generation_failed=True,
            )

        intervention = self.intervention_repo.record(
            event_id=event.event_id,
            scope_type=event.scope_type,
            scope_id=event.scope_id,
            decision=decision,
            selected_memory_ids=list(selected_memory_ids),
            generated_text=generated.text,
            extra_metadata={
                "access_reason": access.reason,
                "group_behavior_probe_ids": list(behavior_memory_ids),
            },
        )
        outbound = OutboundMessage(
            message_id=f"reply:{event.event_id}",
            scope_type=ScopeType.GROUP,
            scope_id=event.scope_id,
            text=generated.text,
            reply_to_message_id=event.message_id,
            dedupe_key=f"reply:{event.event_id}",
            metadata={
                "event_id": event.event_id,
                "intervention_id": str(intervention.id),
                "mode": decision.mode.value if decision.mode else None,
            },
        )
        outbox_id, created = self.outbox_repo.enqueue(outbound)
        return GroupPipelineResult(
            event_id=event.event_id,
            allowed=True,
            access_reason=access.reason,
            primary_action=decision.primary_action,
            mode=decision.mode,
            generated_text=generated.text,
            outbox_id=outbox_id,
            outbox_created=created,
            selected_memory_ids=selected_memory_ids,
        )
