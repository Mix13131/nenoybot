from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app_v2.domain.enums import EventType, MemoryOrigin, MemoryStatus, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.services.memory_mapper import MemoryMapper, _SCHEMA


NOW = datetime(2026, 9, 16, 10, 0, tzinfo=timezone.utc)


class FakeStore:
    def __init__(self) -> None:
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

    def create(self, card):
        self.cards[card.id] = card
        return card

    def update(self, card):
        self.update_calls += 1
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
        self.inputs = []

    def generate_json(self, *args, **kwargs):
        self.calls += 1
        self.inputs.append(args[1])
        if self.error:
            raise self.error
        parsed = self.responses.pop(0)
        return SimpleNamespace(parsed=parsed)


def event(
    text: str,
    *,
    scope=ScopeType.PERSONAL,
    scope_id="u1",
    message_id="m1",
    occurred_at=NOW,
):
    return EventEnvelope(
        event_id=f"evt:{message_id}",
        event_type=EventType.PRIVATE_MESSAGE if scope is ScopeType.PERSONAL else EventType.GROUP_MESSAGE,
        occurred_at=occurred_at,
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
        "payload": {
            "statement_kind": "commitment",
            "status": "open",
            "due_at": None,
            "verbatim": "Завтра отправлю отчёт",
            "claim_kind": "plan",
        },
        "importance": 0.8,
        "confidence": 0.95,
        # Old model fields are intentionally tolerated by fake adapters but
        # ignored by persistence logic.
        "evidence_count": 1,
        "episode_count": 1,
        "usage_policy": {"assist": True, "callback": True, "roast": False, "proactive": True},
    }
    data.update(overrides)
    return data


def _walk_schema(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_schema(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_schema(value)


def test_memory_mapper_strict_schema_closes_every_object_node():
    object_nodes = [node for node in _walk_schema(_SCHEMA) if node.get("type") == "object"]
    assert object_nodes
    for node in object_nodes:
        assert node.get("additionalProperties") is False
        properties = node.get("properties", {})
        assert set(node.get("required", [])) == set(properties)


def test_schema_requires_source_provenance_but_not_model_counts():
    item = _SCHEMA["properties"]["candidates"]["items"]
    assert "source_message_id" in item["required"]
    assert "evidence_excerpt" in item["required"]
    assert "evidence_count" not in item["properties"]
    assert "episode_count" not in item["properties"]
    assert "claim_kind" in item["properties"]["payload"]["required"]


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
    assert card.evidence[0].excerpt == "встреча в субботу в 18:00"


def test_explicit_forget_archives_only_target_in_same_scope():
    store = FakeStore()
    mapper = MemoryMapper(store=store)
    remembered = mapper.map_event(event("Запомни: тестовая тема"))
    memory_id = remembered.written[0].id

    result = mapper.map_event(event("забудь это", message_id="m2"), target_memory_ids=[memory_id, "missing"])

    assert result.forgotten_ids == (memory_id,)
    assert store.cards[memory_id].status is MemoryStatus.ARCHIVED


def test_repeated_commitment_updates_one_logical_card_from_real_sources():
    store = FakeStore()
    adapter = FakeAdapter(responses=[
        {"candidates": [candidate()]},
        {"candidates": [candidate(
            evidence_count=99,
            source_message_id="m2",
            evidence_excerpt="отчёт завтра отправлю",
        )]},
    ])
    mapper = MemoryMapper(store=store, adapter=adapter)

    first = mapper.map_event(event("Завтра отправлю отчёт", message_id="m1"))
    second = mapper.map_event(event("Да, отчёт завтра отправлю", message_id="m2"))

    assert len(store.cards) == 1
    assert first.written[0].id == second.written[0].id
    card = second.written[0]
    assert card.source_count == 2
    assert card.payload["evidence_count"] == 2
    assert len(card.evidence) == 2
    assert card.status is MemoryStatus.ACTIVE
    assert card.confidence >= 0.88


def test_same_source_retry_is_true_noop_without_refresh_or_strengthening():
    store = FakeStore()
    adapter = FakeAdapter(responses=[{"candidates": [candidate()]}, {"candidates": [candidate(confidence=1.0, evidence_count=99)]}])
    mapper = MemoryMapper(store=store, adapter=adapter)
    source = event("Завтра отправлю отчёт", message_id="m1")

    first = mapper.map_event(source).written[0]
    second = mapper.map_event(source).written[0]

    assert second == first
    assert second.source_count == 1
    assert second.payload["episode_count"] == 1
    assert store.update_calls == 0


def test_grounded_commitment_activates_immediately_for_future_callback():
    store = FakeStore()
    adapter = FakeAdapter(responses=[{"candidates": [candidate(confidence=0.90)]}])
    mapper = MemoryMapper(store=store, adapter=adapter)

    card = mapper.map_event(event("Завтра отправлю отчёт")).written[0]

    assert card.status is MemoryStatus.ACTIVE
    assert card.origin is MemoryOrigin.INFERRED
    assert card.confidence == 0.88
    assert card.usage_policy.callback is True
    assert card.usage_policy.proactive is True
    assert card.payload["statement_kind"] == "commitment"
    assert card.payload["verbatim"] == "Завтра отправлю отчёт"
    assert card.payload["claim_kind"] == "plan"


def test_non_statement_inferred_confidence_remains_capped_and_candidate():
    store = FakeStore()
    adapter = FakeAdapter(responses=[{"candidates": [candidate(
        memory_type="observation",
        semantic_key="observation:late",
        confidence=0.99,
        payload={
            "statement_kind": "none",
            "status": "unknown",
            "due_at": None,
            "verbatim": None,
            "claim_kind": "fact",
        },
    )]}])
    mapper = MemoryMapper(store=store, adapter=adapter)

    card = mapper.map_event(event("Сегодня опоздал")).written[0]

    assert card.confidence == 0.70
    assert card.status is MemoryStatus.CANDIDATE
    assert card.origin is MemoryOrigin.INFERRED


def test_model_claimed_pattern_counts_cannot_promote_one_source():
    store = FakeStore()
    adapter = FakeAdapter(responses=[{"candidates": [candidate(
        memory_type="pattern",
        semantic_key="pattern:late",
        evidence_count=99,
        episode_count=99,
        payload={
            "statement_kind": "none",
            "status": "unknown",
            "due_at": None,
            "verbatim": None,
            "claim_kind": "fact",
        },
    )]}])
    mapper = MemoryMapper(store=store, adapter=adapter)

    card = mapper.map_event(event("Сегодня опоздал")).written[0]

    assert card.memory_type == "observation"
    assert card.status is MemoryStatus.CANDIDATE
    assert card.source_count == 1
    assert card.payload["episode_count"] == 1


def test_pattern_promotes_only_after_three_real_sources_two_time_windows():
    store = FakeStore()
    generic_payload = {
        "statement_kind": "none",
        "status": "unknown",
        "due_at": None,
        "verbatim": None,
        "claim_kind": "fact",
    }
    adapter = FakeAdapter(responses=[
        {"candidates": [candidate(memory_type="pattern", semantic_key="pattern:late", payload=generic_payload)]},
        {"candidates": [candidate(memory_type="pattern", semantic_key="pattern:late", payload=generic_payload)]},
        {"candidates": [candidate(memory_type="pattern", semantic_key="pattern:late", payload=generic_payload)]},
    ])
    mapper = MemoryMapper(store=store, adapter=adapter)

    first = mapper.map_event(event("Опоздал", message_id="m1", occurred_at=NOW))
    second = mapper.map_event(event("Снова опоздал", message_id="m2", occurred_at=NOW + timedelta(minutes=20)))
    third = mapper.map_event(event("Опять опоздал", message_id="m3", occurred_at=NOW + timedelta(hours=7)))

    assert first.written[0].status is MemoryStatus.CANDIDATE
    assert second.written[0].status is MemoryStatus.CANDIDATE
    assert third.written[0].memory_type == "pattern"
    assert third.written[0].status is MemoryStatus.ACTIVE
    assert third.written[0].source_count == 3
    assert third.written[0].payload["episode_count"] == 2


def test_long_message_candidates_keep_distinct_grounded_excerpts():
    text = (
        "Проект Альфа: отправка планируется 20 октября, бюджет около 1200 EUR. "
        + "дополнение " * 40
        + "Проект Бета: проверка только через 15 дней после фактического запуска, стоимость 900 USD."
    )
    alpha_excerpt = "отправка планируется 20 октября"
    beta_excerpt = "через 15 дней после фактического запуска"
    generic = {
        "statement_kind": "none",
        "status": "unknown",
        "due_at": None,
        "verbatim": None,
    }
    adapter = FakeAdapter(responses=[{"candidates": [
        candidate(
            memory_type="plan",
            semantic_key="plan:alpha",
            summary="Отправка Альфы планируется 20 октября.",
            source_message_id="m1",
            evidence_excerpt=alpha_excerpt,
            payload={**generic, "claim_kind": "plan"},
        ),
        candidate(
            memory_type="plan",
            semantic_key="plan:beta",
            summary="Проверка Беты зависит от фактического запуска.",
            source_message_id="m1",
            evidence_excerpt=beta_excerpt,
            payload={**generic, "claim_kind": "conditional"},
        ),
    ]}])
    mapper = MemoryMapper(store=FakeStore(), adapter=adapter)

    result = mapper.map_event(event(text))

    assert len(result.written) == 2
    by_key = {card.payload["semantic_key"]: card for card in result.written}
    assert by_key["plan:alpha"].evidence[0].excerpt == alpha_excerpt
    assert by_key["plan:alpha"].payload["claim_kind"] == "plan"
    assert by_key["plan:beta"].evidence[0].excerpt == beta_excerpt
    assert by_key["plan:beta"].payload["claim_kind"] == "conditional"


def test_unvalidated_excerpt_is_rejected_instead_of_fabricated():
    bad = candidate(source_message_id="m1", evidence_excerpt="этого текста здесь нет")
    mapper = MemoryMapper(store=FakeStore(), adapter=FakeAdapter(responses=[{"candidates": [bad]}]))
    result = mapper.map_event(event("Завтра отправлю отчёт"))
    assert result.written == ()


def test_deictic_message_without_context_is_not_mapped_standalone():
    adapter = FakeAdapter(responses=[{"candidates": [candidate()]}])
    mapper = MemoryMapper(store=FakeStore(), adapter=adapter)

    result = mapper.map_event(event("Да, я тоже"))

    assert result.reason == "insufficient_context"
    assert adapter.calls == 0


def test_recent_context_is_structured_and_available_to_mapper():
    adapter = FakeAdapter(responses=[{"candidates": []}])
    mapper = MemoryMapper(store=FakeStore(), adapter=adapter)
    context = [{
        "message_id": "99",
        "author_user_id": "friend",
        "text": "Я люблю настольные игры",
        "created_at": (NOW - timedelta(minutes=1)).isoformat(),
        "reply_to_message_id": None,
    }]

    mapper.map_event(event("Да, я тоже", message_id="100"), recent_context=context)

    assert adapter.calls == 1
    assert '"message_id": "99"' in adapter.inputs[0]
    assert '"text": "Я люблю настольные игры"' in adapter.inputs[0]


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
