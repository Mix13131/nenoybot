from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app_v2.adapters.openai_adapter import OpenAIAdapter
from app_v2.adapters.telegram_sender import TelegramSender
from app_v2.config import AppConfig
from app_v2.domain.enums import EventType, ScopeType
from app_v2.repositories.event_repo import EventRepository
from app_v2.repositories.feedback_repo import FeedbackRepository
from app_v2.repositories.group_context_repo import GroupContextRepository
from app_v2.repositories.group_initiative_repo import GroupInitiativeRepository
from app_v2.repositories.intervention_repo import InterventionRepository
from app_v2.repositories.maintenance_repo import MaintenanceRepository
from app_v2.repositories.memory_repo import MemoryRepository
from app_v2.repositories.message_repo import MessageRepository
from app_v2.repositories.outbox_repo import OutboxRepository
from app_v2.repositories.reminder_repo import ReminderRepository
from app_v2.repositories.task_repo import TaskRepository
from app_v2.repositories.usage_repo import UsageRepository
from app_v2.services.action_engine import ActionEngine
from app_v2.services.context_builder import ContextBuilder
from app_v2.services.feedback_collector import FeedbackCollector
from app_v2.services.group_access import GroupAccessService
from app_v2.services.group_behavior_engine import GroupBehaviorEngine
from app_v2.services.group_initiative import GroupInitiativeService
from app_v2.services.group_pipeline import GroupPipeline
from app_v2.services.memory_mapper import MemoryMapper, MemoryMapperStore
from app_v2.services.personal_pipeline import PersonalPipeline, envelope_from_claimed_event
from app_v2.services.personality_engine import PersonalityEngine
from app_v2.services.response_generator import ResponseGenerator
from app_v2.services.retrieval_engine import RetrievalEngine
from app_v2.services.scene_analyzer import SceneAnalyzer


@dataclass(frozen=True)
class RuntimeComponents:
    event_repo: EventRepository
    outbox_repo: OutboxRepository
    reminder_repo: ReminderRepository
    memory_repo: MemoryRepository
    scene_analyzer: SceneAnalyzer
    personal_pipeline: PersonalPipeline
    group_pipeline: GroupPipeline
    feedback_collector: FeedbackCollector
    action_engine: ActionEngine
    telegram_sender: TelegramSender
    maintenance_repo: MaintenanceRepository


class RuntimeEventHandler:
    """Route a claimed durable event into exactly one v2 product pipeline."""

    def __init__(self, runtime: RuntimeComponents) -> None:
        self.runtime = runtime

    def __call__(self, claimed_event: Any) -> None:
        event = envelope_from_claimed_event(claimed_event)

        # Telegram reactions are feedback transport. They should never provoke a
        # conversational Personal reply merely because the scope is private.
        if event.event_type in {EventType.REACTION_ADDED, EventType.REACTION_REMOVED}:
            self.runtime.feedback_collector.collect(event)
            return

        if event.scope_type is ScopeType.PERSONAL:
            # Replies/negative text may be feedback and still deserve a normal
            # Personal response. Dedupe is owned by FeedbackRepository.
            self.runtime.feedback_collector.collect(event)
            self.runtime.personal_pipeline.process(event)
            return

        self.runtime.group_pipeline.process(event)


def build_runtime(conn: Any, config: AppConfig) -> RuntimeComponents:
    """Build the production object graph on one PostgreSQL connection.

    Domain/services remain unaware of Railway; this is the only process-level
    composition root used by the v2 worker.
    """

    event_repo = EventRepository(conn)
    outbox_repo = OutboxRepository(conn)
    reminder_repo = ReminderRepository(conn)
    task_repo = TaskRepository(conn)
    memory_repo = MemoryRepository(conn)
    message_repo = MessageRepository(conn)
    intervention_repo = InterventionRepository(conn)
    feedback_repo = FeedbackRepository(conn)
    usage_repo = UsageRepository(conn)
    group_context_repo = GroupContextRepository(conn)
    group_initiative_repo = GroupInitiativeRepository(conn)
    maintenance_repo = MaintenanceRepository(conn)

    adapter = OpenAIAdapter(config, usage_repo=usage_repo)
    scene_analyzer = SceneAnalyzer(adapter)
    retrieval_engine = RetrievalEngine(memory_repo)
    memory_mapper = MemoryMapper(
        store=MemoryMapperStore(memory_repo),
        adapter=adapter,
    )
    personality_engine = PersonalityEngine()
    context_builder = ContextBuilder(
        message_repo=message_repo,
        retrieval_engine=retrieval_engine,
    )
    response_generator = ResponseGenerator(adapter=adapter)
    feedback_collector = FeedbackCollector(feedback_repo)

    personal_pipeline = PersonalPipeline(
        scene_analyzer=scene_analyzer,
        memory_mapper=memory_mapper,
        personality_engine=personality_engine,
        context_builder=context_builder,
        response_generator=response_generator,
        intervention_repo=intervention_repo,
        outbox_repo=outbox_repo,
    )

    group_pipeline = GroupPipeline(
        access_service=GroupAccessService(group_context_repo),
        scene_analyzer=scene_analyzer,
        personality_engine=personality_engine,
        context_builder=context_builder,
        response_generator=response_generator,
        intervention_repo=intervention_repo,
        outbox_repo=outbox_repo,
        group_behavior_engine=GroupBehaviorEngine(
            retrieval_engine,
            initiative_service=GroupInitiativeService(group_initiative_repo),
        ),
        feedback_collector=feedback_collector,
        memory_mapper=memory_mapper,
    )

    action_engine = ActionEngine(task_repo=task_repo, reminder_repo=reminder_repo)
    telegram_sender = TelegramSender(token=config.telegram_bot_token)

    return RuntimeComponents(
        event_repo=event_repo,
        outbox_repo=outbox_repo,
        reminder_repo=reminder_repo,
        memory_repo=memory_repo,
        scene_analyzer=scene_analyzer,
        personal_pipeline=personal_pipeline,
        group_pipeline=group_pipeline,
        feedback_collector=feedback_collector,
        action_engine=action_engine,
        telegram_sender=telegram_sender,
        maintenance_repo=maintenance_repo,
    )
