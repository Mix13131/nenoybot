from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app_v2.domain.enums import EventType, MemoryOrigin, MemoryStatus, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.services.memory_mapper import MemoryMapper


class FakeStore:
    def __init__(self) -> None:
        self.cards = {}

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

    def create(self, card):
        self.cards[card.id] = card
        return card

    def update(self, card):
        self.cards[card.id] = card
        return card

    def archive(self, scope_type, scope_id, memory_id):
        card = self.cards.get(memory_id)
        if not card or card.scope_type is not scope_type or card.scope_id != scope_id:
            return False
        self.cards[memory_id] = card.model_copy(update={"status": MemoryStatus.ARCHIVED})
        return True


class FakeAdapter:
    def __init__(self, responses=None, error=None) -> None:
        self.responses = list(responses or [])
        self.error = error
        self.calls = 0

    def generate_json(self, *args, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        parsed = self.responses.pop(0)
        return SimpleNamespace(parsed=parsed)


def event(text: str, *, scope=ScopeType.PERSONAL, scope_id="u1", message_id="m1"):
    return EventEnvelope(
        event_id=f"evt:{message_id}",
        event_type=EventType.PRIVATE_MESSAGE if scope is ScopeType.PERSONAL else EventType.GROUP_MESSAGE,
        occurred_at=datetime.now(timezone.utc),
        scope_type=scope,
        scope_id=scope_id,
        actor_user_id="u1",
        message_id=message_id,
        text=text,
    )


def candidate(**overrides):
    data = {
        "memory_type": "commitment",
        "semantic_key": "commitment:send-report",
        "summary": "Пользователь обещал отправить отчёт завтра.",
        "subject_keys": ["user:u1"],
        "payload": {"deadline_hint": "tomorrow"},
        "importance": 0.8,
        "confidence": 0.95,
        "evidence_count": 1,
        "episode_count": 1,
        "usage_policy": {"assist": True, "callback": True, "roast": False, "proactive": True},
    }
    data.update(overrides)
    return data


def test_explicit_remember_is_deterministic_and_high_confidence():
    store = FakeStore()
    adapter = FakeAdapter(error=AssertionError("model must not be called"))
    mapper = MemoryMapper(store=store, adapter=adapter)

    result = mapper.map_event(event("Запомни: встреча в субботу в 18:00"))

    assert result.failed is False
    assert result.reason == "explicit_remember"
    assert adapter.calls == 0
    card = result.written[0]
    assert card.status is MemoryStatus.ACTIVE
    assert card.origin is MemoryOrigin.EXPLICIT
    assert card.confidence == 0.98
    assert card.scope_type is ScopeType.PERSONAL
    assert card.scope_id == "u1"
    assert len(card.evidence) == 1


def test_explicit_forget_archives_only_target_in_same_scope():
    store = FakeStore()
    mapper = MemoryMapper(store=store)
    remembered = mapper.map_event(event("Запомни: тестовая тема"))
    memory_id = remembered.written[0].id

    result = mapper.map_event(event("забудь это", message_id="m2"), target_memory_ids=[memory_id, "missing"])

    assert result.forgotten_ids == (memory_id,)
    assert store.cards[memory_id].status is MemoryStatus.ARCHIVED


def test_repeated_commitment_updates_one_logical_card():
    store = FakeStore()
    adapter = FakeAdapter(responses=[{"candidates": [candidate()]}, {"candidates": [candidate(evidence_count=2)]}])
    mapper = MemoryMapper(store=store, adapter=adapter)

    first = mapper.map_event(event("Завтра отправлю отчёт", message_id="m1"))
    second = mapper.map_event(event("Да, отчёт завтра отправлю", message_id="m2"))

    assert len(store.cards) == 1
    assert first.written[0].id == second.written[0].id
    card = second.written[0]
    assert card.source_count >= 2
    assert len(card.evidence) == 2


def test_inferred_confidence_is_capped_before_confirmation():
    store = FakeStore()
    adapter = FakeAdapter(responses=[{"candidates": [candidate(confidence=0.99)]}])
    mapper = MemoryMapper(store=store, adapter=adapter)

    card = mapper.map_event(event("Завтра отправлю отчёт")).written[0]

    assert card.confidence == 0.70
    assert card.status is MemoryStatus.CANDIDATE
    assert card.origin is MemoryOrigin.INFERRED


def test_pattern_does_not_promote_before_threshold():
    store = FakeStore()
    adapter = FakeAdapter(responses=[{"candidates": [candidate(
        memory_type="pattern",
        semantic_key="pattern:late",
        evidence_count=1,
        episode_count=1,
    )]}])
    mapper = MemoryMapper(store=store, adapter=adapter)

    card = mapper.map_event(event("Сегодня опоздал")).written[0]

    assert card.memory_type == "observation"
    assert card.status is MemoryStatus.CANDIDATE


def test_pattern_promotes_only_after_three_evidence_two_episodes():
    store = FakeStore()
    adapter = FakeAdapter(responses=[
        {"candidates": [candidate(memory_type="pattern", semantic_key="pattern:late", evidence_count=1, episode_count=1)]},
        {"candidates": [candidate(memory_type="pattern", semantic_key="pattern:late", evidence_count=3, episode_count=2)]},
    ])
    mapper = MemoryMapper(store=store, adapter=adapter)

    first = mapper.map_event(event("Опоздал", message_id="m1"))
    second = mapper.map_event(event("Снова опоздал", message_id="m2"))

    assert first.written[0].status is MemoryStatus.CANDIDATE
    assert second.written[0].memory_type == "pattern"
    assert second.written[0].status is MemoryStatus.ACTIVE


def test_mapper_failure_is_safe_noop():
    mapper = MemoryMapper(store=FakeStore(), adapter=FakeAdapter(error=RuntimeError("boom")))

    result = mapper.map_event(event("Обычное сообщение"))

    assert result.failed is True
    assert result.written == ()
    assert result.reason.startswith("mapper_failure:")


def test_group_scope_is_preserved():
    store = FakeStore()
    adapter = FakeAdapter(responses=[{"candidates": [candidate()]}])
    mapper = MemoryMapper(store=store, adapter=adapter)

    card = mapper.map_event(event("Завтра отправлю отчёт", scope=ScopeType.GROUP, scope_id="g1")).written[0]

    assert card.scope_type is ScopeType.GROUP
    assert card.scope_id == "g1"
