from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

from app_v2.domain.enums import EventType, MemoryStatus, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.services.memory_mapper import MemoryMapper


NOW = datetime(2026, 9, 17, 9, 0, tzinfo=timezone.utc)


class FakeStore:
    def __init__(self):
        self.cards = {}
        self.update_calls = 0

    def find_semantic_match(self, scope_type, scope_id, semantic_key):
        for card in self.cards.values():
            if (
                card.scope_type is scope_type
                and card.scope_id == scope_id
                and card.status in {MemoryStatus.CANDIDATE, MemoryStatus.ACTIVE}
                and card.payload.get("semantic_key") == semantic_key
            ):
                return card
        return None

    def find_source_identity_matches(self, scope_type, scope_id, evidence):
        identity = (evidence.message_id, evidence.author_id, evidence.timestamp)
        found = []
        for card in self.cards.values():
            if card.scope_type is not scope_type or card.scope_id != scope_id:
                continue
            if any((item.message_id, item.author_id, item.timestamp) == identity for item in card.evidence):
                found.append(card)
        return tuple(found)

    @contextmanager
    def source_lock(self, *args, **kwargs):
        yield

    @contextmanager
    def semantic_locks(self, *args, **kwargs):
        yield

    def create(self, card):
        self.cards[card.id] = card
        return card

    def update(self, card):
        self.update_calls += 1
        self.cards[card.id] = card
        return card

    def archive(self, scope_type, scope_id, memory_id):
        return False


def _event(
    text,
    *,
    actor="u1",
    message_id="42",
    occurred_at=NOW,
    scope=ScopeType.PERSONAL,
    event_type=None,
):
    return EventEnvelope(
        event_id=f"evt:{message_id}:{text[:6]}",
        event_type=event_type or (EventType.PRIVATE_MESSAGE if scope is ScopeType.PERSONAL else EventType.GROUP_MESSAGE),
        occurred_at=occurred_at,
        scope_type=scope,
        scope_id="u-scope" if scope is ScopeType.PERSONAL else "-100777",
        actor_user_id=actor,
        message_id=message_id,
        text=text,
    )


def _candidate(text, *, semantic_key="plan:shipment", source="42", confidence=0.6, subject_keys=None):
    return {
        "memory_type": "plan",
        "semantic_key": semantic_key,
        "summary": text,
        "subject_keys": list(subject_keys or []),
        "source_message_id": source,
        "evidence_excerpt": text,
        "payload": {
            "statement_kind": "none",
            "status": "unknown",
            "due_at": None,
            "verbatim": text,
            "claim_kind": "plan",
        },
        "importance": 0.7,
        "confidence": confidence,
        "usage_policy": {
            "assist": True,
            "callback": True,
            "roast": False,
            "proactive": False,
        },
    }


def test_edited_same_source_replaces_evidence_without_new_corroboration() -> None:
    store = FakeStore()
    mapper = MemoryMapper(store=store)

    first_event = _event("Отправка планируется 20 октября")
    first = mapper._upsert_candidate(
        first_event,
        _candidate(first_event.text),
        explicit=False,
        recent_context=(),
    )
    assert first is not None

    edited_event = _event("Отправка отменена", event_type=EventType.EDITED_MESSAGE)
    edited = mapper._upsert_candidate(
        edited_event,
        _candidate(edited_event.text, confidence=0.99),
        explicit=False,
        recent_context=(),
    )
    assert edited is not None
    assert edited.source_count == 1
    assert edited.payload["evidence_count"] == 1
    assert edited.payload["episode_count"] == 1
    assert len(edited.evidence) == 1
    assert edited.evidence[0].excerpt == "Отправка отменена"
    assert edited.summary == "Отправка отменена"
    assert edited.confidence == first.confidence
    assert edited.last_confirmed_at == first.last_confirmed_at

    retry = mapper._upsert_candidate(
        edited_event,
        _candidate(edited_event.text, confidence=1.0),
        explicit=False,
        recent_context=(),
    )
    assert retry is None
    assert store.update_calls == 1


def test_edit_can_change_semantic_key_without_leaving_old_card() -> None:
    store = FakeStore()
    mapper = MemoryMapper(store=store)

    first_event = _event("Запомни: встреча во вторник")
    first_candidate = _candidate(
        "встреча во вторник",
        semantic_key="explicit:old-key",
        subject_keys=["user:u1"],
    )
    first_candidate["memory_type"] = "observation"
    first = mapper._upsert_candidate(
        first_event,
        first_candidate,
        explicit=True,
        recent_context=(),
    )
    assert first is not None

    edited_event = _event(
        "Запомни: встреча отменена",
        event_type=EventType.EDITED_MESSAGE,
    )
    edited_candidate = _candidate(
        "встреча отменена",
        semantic_key="explicit:new-key",
        confidence=0.99,
        subject_keys=["user:u1"],
    )
    edited_candidate["memory_type"] = "observation"
    edited = mapper._upsert_candidate(
        edited_event,
        edited_candidate,
        explicit=True,
        recent_context=(),
    )

    assert edited is not None
    assert edited.id == first.id
    assert len(store.cards) == 1
    assert edited.payload["semantic_key"] == "explicit:new-key"
    assert edited.summary == "встреча отменена"
    assert edited.evidence[0].excerpt == "встреча отменена"
    assert edited.source_count == 1


def test_context_candidate_subject_comes_from_trusted_source_author() -> None:
    store = FakeStore()
    mapper = MemoryMapper(store=store)
    bob_reply = _event(
        "Да, я тоже",
        actor="bob",
        message_id="101",
        occurred_at=datetime(2026, 9, 17, 9, 5, tzinfo=timezone.utc),
        scope=ScopeType.GROUP,
    )
    context = (
        {
            "message_id": "100",
            "author_user_id": "alice",
            "created_at": "2026-09-17T09:00:00+00:00",
            "reply_to_message_id": None,
            "text": "Люблю утренние пробежки",
        },
    )
    candidate = _candidate(
        "Люблю утренние пробежки",
        semantic_key="preference:morning-run",
        source="100",
        subject_keys=["topic:fitness"],
    )
    candidate["memory_type"] = "preference"
    candidate["payload"]["claim_kind"] = "fact"

    card = mapper._upsert_candidate(
        bob_reply,
        candidate,
        explicit=False,
        recent_context=context,
    )
    assert card is not None
    assert "user:alice" in card.subject_keys
    assert "user:bob" not in card.subject_keys
    assert "topic:fitness" in card.subject_keys


def test_model_cannot_override_trusted_source_user() -> None:
    mapper = MemoryMapper(store=FakeStore())
    bob_reply = _event(
        "Да, я тоже",
        actor="bob",
        message_id="101",
        occurred_at=datetime(2026, 9, 17, 9, 5, tzinfo=timezone.utc),
        scope=ScopeType.GROUP,
    )
    context = (
        {
            "message_id": "100",
            "author_user_id": "alice",
            "created_at": "2026-09-17T09:00:00+00:00",
            "reply_to_message_id": None,
            "text": "Люблю утренние пробежки",
        },
    )
    candidate = _candidate(
        "Люблю утренние пробежки",
        semantic_key="preference:morning-run",
        source="100",
        subject_keys=["user:bob"],
    )
    candidate["memory_type"] = "preference"

    assert mapper._upsert_candidate(
        bob_reply,
        candidate,
        explicit=False,
        recent_context=context,
    ) is None
