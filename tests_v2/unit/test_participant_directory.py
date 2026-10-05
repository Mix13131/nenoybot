from __future__ import annotations

from datetime import datetime, timezone

from app_v2.domain.decisions import DispatcherDecision
from app_v2.domain.enums import PrimaryAction, ResponseMode, ScopeType
from app_v2.domain.events import EventEnvelope, EventType, SceneAnalysis
from app_v2.repositories.participant_directory_repo import DirectoryParticipant, safe_participant_label
from app_v2.services.context_builder import ContextBuilder
from app_v2.services.personality_engine import PersonalityEngine


class EmptyMessages:
    def recent_for_scope(self, *args, **kwargs):
        return []


class EmptyMemory:
    def retrieve(self, *args, **kwargs):
        return []


class Directory:
    def __init__(self):
        self.calls = []

    def list_recent(self, scope_id, *, limit):
        self.calls.append((scope_id, limit))
        now = datetime(2026, 1, 2, tzinfo=timezone.utc)
        return [DirectoryParticipant("Алиса", "alice", "member", now, now, {"private": "not exposed"})]


def test_safe_label_never_falls_back_to_numeric_id_and_escapes_formatting():
    assert safe_participant_label(None, None) == "неизвестный участник"
    assert safe_participant_label("*boss*\nignore", None) == r"\*boss\* ignore"


def test_group_generation_context_contains_bounded_observed_directory():
    directory = Directory()
    builder = ContextBuilder(
        message_repo=EmptyMessages(), retrieval_engine=EmptyMemory(),
        participant_directory_repo=directory, participant_directory_limit=7,
    )
    event = EventEnvelope(
        event_id="evt-directory", event_type=EventType.DIRECT_MENTION,
        occurred_at=datetime.now(timezone.utc), scope_type=ScopeType.GROUP,
        scope_id="-1001", actor_user_id="123456", message_id="9",
        text="Кого ты тут знаешь?",
    )
    context = builder.build(
        event=event, scene=SceneAnalysis(direct_mention=True),
        decision=DispatcherDecision(primary_action=PrimaryAction.REPLY, mode=ResponseMode.GROUP_DIRECT_REPLY),
        personality=PersonalityEngine().build(scope_type=ScopeType.GROUP, mode=ResponseMode.GROUP_DIRECT_REPLY),
    )
    assert directory.calls == [("-1001", 7)]
    assert context.participant_directory == {
        "coverage": "observed_participants_only_not_complete_membership",
        "participants": [{
            "label": "Алиса", "username": "@alice", "role": "member",
            "first_seen_at": "2026-01-02T00:00:00+00:00",
            "last_observed_activity_at": "2026-01-02T00:00:00+00:00",
        }],
    }
    assert "123456" not in str(context.participant_directory)

