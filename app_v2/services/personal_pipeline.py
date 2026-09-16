from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from app_v2.domain.enums import PrimaryAction, ResponseMode, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.domain.outbound import OutboundMessage
from app_v2.services.dispatcher import decide


class PersonalPipelineError(RuntimeError):
    pass


@dataclass(frozen=True)
class PersonalPipelineResult:
    event_id: str
    mode: ResponseMode | None
    generated_text: str | None
    outbox_id: int | None
    outbox_created: bool
    memory_written_ids: tuple[str, ...] = ()
    memory_forgotten_ids: tuple[str, ...] = ()
    generation_failed: bool = False


class PersonalPipeline:
    """First real Personal v2 orchestration slice. No direct Telegram sending here."""

    def __init__(
        self,
        *,
        scene_analyzer: Any,
        memory_mapper: Any,
        personality_engine: Any,
        context_builder: Any,
        response_generator: Any,
        intervention_repo: Any,
        outbox_repo: Any,
    ) -> None:
        self.scene_analyzer = scene_analyzer
        self.memory_mapper = memory_mapper
        self.personality_engine = personality_engine
        self.context_builder = context_builder
        self.response_generator = response_generator
        self.intervention_repo = intervention_repo
        self.outbox_repo = outbox_repo

    def process(self, event: EventEnvelope) -> PersonalPipelineResult:
        if event.scope_type is not ScopeType.PERSONAL:
            raise PersonalPipelineError("PersonalPipeline accepts only personal scope events")

        scene = self.scene_analyzer.analyze(event)
        decision = decide(event, scene)
        if decision.primary_action is not PrimaryAction.REPLY:
            self.intervention_repo.record(
                event_id=event.event_id,
                scope_type=event.scope_type,
                scope_id=event.scope_id,
                decision=decision,
                selected_memory_ids=[],
                generated_text=None,
            )
            return PersonalPipelineResult(
                event_id=event.event_id,
                mode=decision.mode,
                generated_text=None,
                outbox_id=None,
                outbox_created=False,
            )

        target_memory_ids = self._target_memory_ids(event)
        mapper_context = self._mapper_context(event)
        mapper_result = self.memory_mapper.map_event(
            event,
            target_memory_ids=target_memory_ids,
            recent_context=mapper_context,
        )

        memory_usage = "callback" if decision.mode is ResponseMode.MIRROR else "assist"
        personality = self.personality_engine.build(
            scope_type=event.scope_type,
            mode=decision.mode or ResponseMode.ASSISTANT,
            scene=scene,
        )
        context = self.context_builder.build(
            event=event,
            scene=scene,
            decision=decision,
            personality=personality,
            subject_keys=self._subject_keys(event),
            memory_usage=memory_usage,
        )

        selected_memory_ids = [memory.id for memory in context.memories]
        try:
            generated = self.response_generator.generate(context)
        except Exception as exc:
            self.intervention_repo.record(
                event_id=event.event_id,
                scope_type=event.scope_type,
                scope_id=event.scope_id,
                decision=decision,
                selected_memory_ids=selected_memory_ids,
                generated_text=None,
                extra_metadata={"generation_failed": True, "error_type": type(exc).__name__},
            )
            return PersonalPipelineResult(
                event_id=event.event_id,
                mode=decision.mode,
                generated_text=None,
                outbox_id=None,
                outbox_created=False,
                memory_written_ids=tuple(card.id for card in mapper_result.written),
                memory_forgotten_ids=tuple(mapper_result.forgotten_ids),
                generation_failed=True,
            )

        intervention = self.intervention_repo.record(
            event_id=event.event_id,
            scope_type=event.scope_type,
            scope_id=event.scope_id,
            decision=decision,
            selected_memory_ids=selected_memory_ids,
            generated_text=generated.text,
        )

        outbound = OutboundMessage(
            message_id=f"reply:{event.event_id}",
            scope_type=event.scope_type,
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
        return PersonalPipelineResult(
            event_id=event.event_id,
            mode=decision.mode,
            generated_text=generated.text,
            outbox_id=outbox_id,
            outbox_created=created,
            memory_written_ids=tuple(card.id for card in mapper_result.written),
            memory_forgotten_ids=tuple(mapper_result.forgotten_ids),
        )

    def _mapper_context(self, event: EventEnvelope) -> tuple[dict[str, Any], ...]:
        builder = getattr(self.context_builder, "mapper_context", None)
        if builder is None:
            return ()
        try:
            value = builder(event)
        except Exception:
            return ()
        return tuple(item for item in value if isinstance(item, dict))

    @staticmethod
    def _subject_keys(event: EventEnvelope) -> list[str]:
        return [f"user:{event.actor_user_id}"] if event.actor_user_id else []

    @staticmethod
    def _target_memory_ids(event: EventEnvelope) -> Iterable[str]:
        value = event.metadata.get("target_memory_ids", [])
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        return []


def envelope_from_claimed_event(claimed_event: Any) -> EventEnvelope:
    """Restore the normalized EventEnvelope persisted by Telegram ingest."""
    payload = dict(claimed_event.payload)
    payload.setdefault("event_id", claimed_event.event_id)
    payload.setdefault("event_type", claimed_event.event_type)
    payload.setdefault("scope_type", claimed_event.scope_type)
    payload.setdefault("scope_id", claimed_event.scope_id)
    payload.setdefault("actor_user_id", claimed_event.actor_user_id)
    payload.setdefault("occurred_at", claimed_event.created_at)
    return EventEnvelope.model_validate(payload)


def make_personal_event_handler(pipeline: PersonalPipeline):
    """Return an EventWorker-compatible handler for Personal events."""
    def handle(claimed_event: Any) -> None:
        if str(claimed_event.scope_type) != ScopeType.PERSONAL.value:
            return
        pipeline.process(envelope_from_claimed_event(claimed_event))

    return handle
