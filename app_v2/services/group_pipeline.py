from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app_v2.domain.enums import EventType, PrimaryAction, ResponseMode, ScopeType
from app_v2.domain.events import EventEnvelope, SceneAnalysis
from app_v2.domain.outbound import OutboundMessage
from app_v2.services.dispatcher import DispatcherPolicyState, decide
from app_v2.services.group_silence_wakeup import silence_wakeup_window_open
from app_v2.services.operation_receipts import group_operation_receipts


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
    mapped_memory_ids: tuple[str, ...] = ()
    generation_failed: bool = False


def _should_map_group_memory(event: EventEnvelope, scene: SceneAnalysis) -> bool:
    text = (event.text or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if lowered.startswith(("запомни", "remember")) or lowered in {"забудь", "забудь это", "forget it", "forget this"}:
        return True
    return any(
        (
            scene.memory_value >= 0.35,
            scene.banter_score >= 0.80,
            scene.contradiction_score >= 0.65,
            scene.commitment_signal >= 0.60,
            scene.decision_signal >= 0.60,
        )
    )


class GroupPipeline:
    """Group orchestration for approved test groups."""

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
        feedback_collector: Any | None = None,
        memory_mapper: Any | None = None,
        group_reminder_service: Any | None = None,
        scheduled_action_interpreter: Any | None = None,
        silence_wakeup_guard: Any | None = None,
        connector_resolver: Any | None = None,
        url_reader: Any | None = None,
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
        self.feedback_collector = feedback_collector
        self.memory_mapper = memory_mapper
        self.group_reminder_service = group_reminder_service
        self.scheduled_action_interpreter = scheduled_action_interpreter
        self.silence_wakeup_guard = silence_wakeup_guard
        self.connector_resolver = connector_resolver
        self.url_reader = url_reader
        self.unsolicited_enabled = unsolicited_enabled

    def _mapper_context(self, event: EventEnvelope) -> tuple[dict[str, Any], ...]:
        builder = getattr(self.context_builder, "mapper_context", None)
        if builder is None:
            return ()
        try:
            value = builder(event)
        except Exception:
            return ()
        return tuple(item for item in value if isinstance(item, dict))

    def _target_memory_ids(self, event: EventEnvelope) -> tuple[str, ...]:
        value = event.metadata.get("target_memory_ids", [])
        if isinstance(value, list):
            explicit = tuple(str(item) for item in value if str(item).strip())
            if explicit:
                return explicit

        # selected_memory_ids on an intervention are the full retrieval/context
        # set used during generation, not a verified list of facts mentioned in
        # the sent text. Without explicit referenced-memory provenance, reply
        # bound "забудь это" is ambiguous and must ask for clarification rather
        # than deleting unrelated context memories.
        return ()

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

        connector_config = None
        if self.connector_resolver is not None:
            try:
                connector_config = self.connector_resolver.resolve(access.context)
            except Exception:
                return GroupPipelineResult(
                    event_id=event.event_id,
                    allowed=True,
                    access_reason="connector_unavailable",
                    primary_action=PrimaryAction.IGNORE,
                )

        if event.event_type is EventType.GROUP_SILENCE_WAKEUP:
            try:
                expected_last_human_id = int(
                    event.metadata.get("last_human_message_id")
                )
            except (TypeError, ValueError):
                return GroupPipelineResult(
                    event_id=event.event_id,
                    allowed=True,
                    access_reason="silence_wakeup_invalid_boundary",
                    primary_action=PrimaryAction.IGNORE,
                )
            if self.silence_wakeup_guard is None:
                return GroupPipelineResult(
                    event_id=event.event_id,
                    allowed=True,
                    access_reason="silence_wakeup_guard_unavailable",
                    primary_action=PrimaryAction.IGNORE,
                )
            try:
                still_current = self.silence_wakeup_guard.is_current_episode(
                    event.scope_id,
                    expected_last_human_id,
                )
            except Exception:
                still_current = False
            if not still_current:
                return GroupPipelineResult(
                    event_id=event.event_id,
                    allowed=True,
                    access_reason="silence_wakeup_stale",
                    primary_action=PrimaryAction.IGNORE,
                )
            if not silence_wakeup_window_open(
                dict(access.context.profile or {}),
                now=current,
                connector_config=connector_config,
            ):
                return GroupPipelineResult(
                    event_id=event.event_id,
                    allowed=True,
                    access_reason="silence_wakeup_window_closed",
                    primary_action=PrimaryAction.IGNORE,
                )

        if self.feedback_collector is not None:
            self.feedback_collector.collect(event)
            if event.event_type in {EventType.REACTION_ADDED, EventType.REACTION_REMOVED}:
                return GroupPipelineResult(
                    event_id=event.event_id,
                    allowed=True,
                    access_reason="feedback_collected",
                    primary_action=PrimaryAction.IGNORE,
                )

        scheduled_action_interpretation = None
        if self.scheduled_action_interpreter is not None:
            try:
                scheduled_action_interpretation = self.scheduled_action_interpreter.interpret(event)
            except Exception:
                scheduled_action_interpretation = None

        reminder_action_state: dict[str, Any] | None = None
        if self.group_reminder_service is not None:
            try:
                cancelled_count = self.group_reminder_service.cancel_on_response(event)
                reminder_action = self.group_reminder_service.maybe_schedule(
                    event,
                    now=current,
                    interpreted_action=scheduled_action_interpretation,
                )
                if reminder_action is not None:
                    reminder_action_state = reminder_action.as_action_state()
                elif cancelled_count:
                    reminder_action_state = {
                        "status": "cancelled_on_response",
                        "cancelled_count": cancelled_count,
                    }
            except Exception as exc:
                recover = getattr(
                    self.group_reminder_service,
                    "recover_failed_transaction",
                    None,
                )
                if callable(recover):
                    recover()
                reminder_action_state = {
                    "status": "error",
                    "reason": type(exc).__name__,
                }

        group_context = access.context
        if event.event_type is EventType.GROUP_SILENCE_WAKEUP:
            last_human_excerpt = str(
                event.metadata.get("last_human_excerpt") or ""
            ).strip()
            scene = self.scene_analyzer.analyze(
                event,
                recent_context=last_human_excerpt or None,
            )
            # The excerpt is historical safety/context evidence, not a new
            # human turn. Never let an old question/command promote this
            # synthetic event into the explicit path and bypass unsolicited
            # guardrails.
            scene = scene.model_copy(
                update={
                    "direct_mention": False,
                    "reply_to_bot": False,
                    "question_to_bot": False,
                    "command_intent": None,
                    "memory_value": 0.0,
                    "commitment_signal": 0.0,
                    "decision_signal": 0.0,
                }
            )
        else:
            scene = self.scene_analyzer.analyze(event)

        # Reminder stop commands are operational controls, not a request to mute
        # НеНой. Phrases like "горшочек, не вари" or "достаточно напоминать"
        # can look like a generic mute intent to the scene classifier. If the
        # deterministic reminder action already recognized the command, preserve
        # the conversational reply path and acknowledge the real cancellation.
        if reminder_action_state and reminder_action_state.get("status") in {"cancelled", "not_cancelled"}:
            scene = scene.model_copy(update={"command_intent": "cancel_reminder"})

        profile = (
            connector_config.personality.as_context_profile(
                connector_config.identity.preset
            )
            if connector_config is not None
            else dict(group_context.profile or {})
        )
        participant_profile = dict(group_context.participant.profile or {})
        adaptation = participant_profile.get("personality_modifiers")
        if not isinstance(adaptation, dict):
            adaptation = {}
        memory_usage = "assist"
        callback_fatigue_minutes = 60
        behavior_memory_ids: tuple[str, ...] = ()
        statement_watch_state: dict[str, Any] | None = None

        # Retrieve old memory before mapping the current message so a callback
        # can never be manufactured from the line it is reacting to.
        if self.group_behavior_engine is not None:
            plan_kwargs = {
                "event": event,
                "group_context": group_context,
                "scene": scene,
                "now": current,
            }
            if connector_config is not None:
                plan_kwargs["connector_config"] = connector_config
            plan = self.group_behavior_engine.plan(**plan_kwargs)
            scene = plan.scene
            state = plan.state
            profile = dict(plan.context_profile)
            adaptation = dict(plan.participant_adaptation)
            memory_usage = plan.memory_usage
            callback_fatigue_minutes = plan.callback_fatigue_minutes
            behavior_memory_ids = tuple(plan.callback_memory_ids)
            statement_watch_state = plan.statement_watch
        else:
            muted = bool(group_context.silent_until and group_context.silent_until > current)
            connector_unsolicited = (
                connector_config.behavior.unsolicited_enabled
                if connector_config is not None
                else self.unsolicited_enabled
            )
            connector_initiative = (
                connector_config.behavior.initiative
                if connector_config is not None
                else int(profile.get("initiative", 6) or 6)
            )
            state = DispatcherPolicyState(
                group_muted=muted,
                cooldown_active=(not connector_unsolicited),
                initiative_level=connector_initiative,
                allow_roast=False,
                allow_callbacks=False,
                metadata={
                    "group_profile": profile.get("profile", "friends"),
                    "connector": (
                        connector_config.public_state()
                        if connector_config is not None
                        else None
                    ),
                },
            )

        mapped_memory_ids: tuple[str, ...] = ()
        mapper_result: Any | None = None
        memory_attempted = False
        should_map_memory = (
            event.event_type is EventType.EDITED_MESSAGE
            or _should_map_group_memory(event, scene)
        )
        if self.memory_mapper is not None and should_map_memory:
            memory_attempted = True
            mapper_result = self.memory_mapper.map_event(
                event,
                target_memory_ids=self._target_memory_ids(event),
                recent_context=self._mapper_context(event),
            )
            mapped_memory_ids = tuple(card.id for card in mapper_result.written)

        operation_receipts = group_operation_receipts(
            mapper_result,
            memory_attempted=memory_attempted,
            reminder_action_state=reminder_action_state,
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
                    "mapped_memory_ids": list(mapped_memory_ids),
                    "reminder_action": reminder_action_state,
                    "scheduled_action_interpretation": (
                        scheduled_action_interpretation.as_action_state()
                        if scheduled_action_interpretation is not None
                        else None
                    ),
                    "statement_watch": statement_watch_state,
                    "operation_receipts": operation_receipts,
                },
            )
            return GroupPipelineResult(
                event_id=event.event_id,
                allowed=True,
                access_reason=access.reason,
                primary_action=decision.primary_action,
                mode=decision.mode,
                selected_memory_ids=behavior_memory_ids,
                mapped_memory_ids=mapped_memory_ids,
            )

        url_read_state: dict[str, Any] | None = None
        url_read_telemetry: dict[str, Any] | None = None
        external_context: tuple[dict[str, Any], ...] = ()
        if self.url_reader is not None:
            try:
                url_bundle = self.url_reader.read_for_event(event)
                if url_bundle.status != "not_requested":
                    url_read_state = url_bundle.as_action_state()
                    url_read_telemetry = url_bundle.as_telemetry()
                    external_context = url_bundle.external_context()
            except Exception as exc:
                url_read_state = {
                    "status": "failed",
                    "reason": "reader_error",
                    "detail": type(exc).__name__,
                }
                url_read_telemetry = dict(url_read_state)

        personality = self.personality_engine.build(
            scope_type=ScopeType.GROUP,
            mode=decision.mode or ResponseMode.GROUP_DIRECT_REPLY,
            scene=scene,
            context_profile=profile,
            participant_adaptation=adaptation,
        )
        subject_keys = [f"user:{event.actor_user_id}"] if event.actor_user_id else []
        action_state: dict[str, Any] = {
            "group_title": group_context.title,
            "participant_role": group_context.participant.role,
            "connector": (
                connector_config.public_state()
                if connector_config is not None
                else None
            ),
            "behavior_probe_ids": list(behavior_memory_ids),
            "mapped_memory_ids": list(mapped_memory_ids),
            "operation_receipts": operation_receipts,
        }
        if url_read_state is not None:
            action_state["url_read"] = url_read_state
        if reminder_action_state is not None:
            action_state["group_reminder"] = reminder_action_state
        if scheduled_action_interpretation is not None:
            action_state["scheduled_action_interpretation"] = (
                scheduled_action_interpretation.as_action_state()
            )
        if statement_watch_state is not None:
            action_state["statement_watch"] = statement_watch_state
        if event.event_type is EventType.REMINDER_DUE:
            action_state["reminder_due"] = dict(event.metadata.get("reminder_payload") or {})

        context_kwargs: dict[str, Any] = {
            "event": event,
            "scene": scene,
            "decision": decision,
            "personality": personality,
            "subject_keys": subject_keys,
            "memory_usage": memory_usage,
            "callback_fatigue_minutes": callback_fatigue_minutes,
            "action_state": action_state,
        }
        if external_context:
            context_kwargs["external_context"] = external_context
        context = self.context_builder.build(**context_kwargs)
        if context.scope_type is not ScopeType.GROUP or context.scope_id != event.scope_id:
            raise GroupPipelineError("Context Builder returned cross-scope Group context")

        context_memory_ids = tuple(memory.id for memory in context.memories)
        selected_memory_ids = tuple(dict.fromkeys((*behavior_memory_ids, *context_memory_ids)))
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
                    "mapped_memory_ids": list(mapped_memory_ids),
                    "reminder_action": reminder_action_state,
                    "scheduled_action_interpretation": (
                        scheduled_action_interpretation.as_action_state()
                        if scheduled_action_interpretation is not None
                        else None
                    ),
                    "statement_watch": statement_watch_state,
                    "operation_receipts": operation_receipts,
                    "url_read": url_read_telemetry,
                },
            )
            return GroupPipelineResult(
                event_id=event.event_id,
                allowed=True,
                access_reason=access.reason,
                primary_action=decision.primary_action,
                mode=decision.mode,
                selected_memory_ids=selected_memory_ids,
                mapped_memory_ids=mapped_memory_ids,
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
                "mapped_memory_ids": list(mapped_memory_ids),
                "reminder_action": reminder_action_state,
                "statement_watch": statement_watch_state,
                "operation_receipts": operation_receipts,
                "url_read": url_read_telemetry,
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
                "message_thread_id": event.metadata.get("message_thread_id"),
            },
        )
        outbox_id, created = self.outbox_repo.enqueue(outbound)
        if created and self.group_behavior_engine is not None and behavior_memory_ids:
            self.group_behavior_engine.mark_callback_memories_used(
                event.scope_id,
                behavior_memory_ids,
            )
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
            mapped_memory_ids=mapped_memory_ids,
        )