from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app_v2.domain.enums import EventType, MemoryOrigin, MemoryStatus, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.services.memory_mapper import MemoryMapper, _SCHEMA


NOW = datetime(2026, 9, 16, 10, 0, tzinfo=timezone.utc)


class FakeStore:
    def __init__(self, *, fail_create_at: int | None = None) -> None:
        self.cards = {}
        self.update_calls = 0
        self.create_calls = 0
        self.fail_create_at = fail_create_at

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

    def find_semantic_match_for_subject(
        self,
        scope_type,
        scope_id,
        semantic_key,
        subject_user_key,
    ):
        for card in self.cards.values():
            if (
                card.scope_type is scope_type
                and card.scope_id == scope_id
                and card.status in {MemoryStatus.CANDIDATE, MemoryStatus.ACTIVE}
                and card.payload.get("semantic_key") == semantic_key
                and subject_user_key in card.subject_keys
            ):
                return card
        return None

    def find_source_identity_matches(self, scope_type, scope_id, evidence):
        return tuple(
            card for card in self.cards.values()
            if card.scope_type is scope_type
            and card.scope_id == scope_id
            and card.status in {MemoryStatus.CANDIDATE, MemoryStatus.ACTIVE}
            and any(
                item.message_id == evidence.message_id
                and item.author_id == evidence.author_id
                for item in card.evidence
            )
        )

    def create(self, card):
        self.create_calls += 1
        if self.fail_create_at == self.create_calls:
            raise RuntimeError("simulated create failure")
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
    actor_user_id="u1",
):
    return EventEnvelope(
        event_id=f"evt:{message_id}",
        event_type=EventType.PRIVATE_MESSAGE if scope is ScopeType.PERSONAL else EventType.GROUP_MESSAGE,
        occurred_at=occurred_at,
        scope_type=scope,
        scope_id=scope_id,
        actor_user_id=actor_user_id,
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


def test_same_source_retry_is_true_noop_without_refresh_or_write_receipt():
    store = FakeStore()
    adapter = FakeAdapter(responses=[{"candidates": [candidate()]}, {"candidates": [candidate(confidence=1.0, evidence_count=99)]}])
    mapper = MemoryMapper(store=store, adapter=adapter)
    source = event("Завтра отправлю отчёт", message_id="m1")

    first_result = mapper.map_event(source)
    first = first_result.written[0]
    second_result = mapper.map_event(source)

    assert second_result.written == ()
    persisted = store.cards[first.id]
    assert persisted == first
    assert persisted.source_count == 1
    assert persisted.payload["episode_count"] == 1
    assert store.update_calls == 0


def test_edited_multi_card_source_reconciles_all_slots_and_archives_stale_cards():
    store = FakeStore()
    original = "Альфа отправится 20-го. Бета стоит 900 USD."
    edited = "Альфа отменена. Бета стоит 750 USD."
    adapter = FakeAdapter(responses=[
        {"candidates": [
            candidate(semantic_key="alpha:planned", summary="Альфа запланирована.", evidence_excerpt="Альфа отправится 20-го"),
            candidate(semantic_key="beta:900", summary="Бета стоит 900 USD.", evidence_excerpt="Бета стоит 900 USD"),
        ]},
        {"candidates": [
            candidate(semantic_key="alpha:cancelled", summary="Альфа отменена.", evidence_excerpt="Альфа отменена"),
            candidate(semantic_key="beta:750", summary="Бета стоит 750 USD.", evidence_excerpt="Бета стоит 750 USD"),
        ]},
    ])
    mapper = MemoryMapper(store=store, adapter=adapter)
    first = mapper.map_event(event(original))
    edit_event = event(edited).model_copy(update={"event_type": EventType.EDITED_MESSAGE})

    corrected = mapper.map_event(edit_event)

    assert corrected.failed is False
    assert len(corrected.written) == 2
    assert {card.id for card in corrected.written} == {card.id for card in first.written}
    assert {card.payload["semantic_key"] for card in corrected.written} == {"alpha:cancelled", "beta:750"}
    assert {card.evidence[0].excerpt for card in corrected.written} == {"Альфа отменена", "Бета стоит 750 USD"}
    assert all(card.source_count == 1 for card in corrected.written)
    assert all(card.status is not MemoryStatus.ARCHIVED for card in store.cards.values())


def test_partial_write_preserves_already_committed_cards_in_mapper_result():
    store = FakeStore(fail_create_at=2)
    second_candidate = candidate(
        memory_type="plan",
        semantic_key="plan:call-client",
        summary="Пользователь планирует позвонить клиенту вечером.",
        source_message_id="m1",
        evidence_excerpt="Позвоню клиенту вечером",
        payload={
            "statement_kind": "none",
            "status": "unknown",
            "due_at": None,
            "verbatim": None,
            "claim_kind": "plan",
        },
    )
    adapter = FakeAdapter(responses=[{"candidates": [candidate(), second_candidate]}])
    mapper = MemoryMapper(store=store, adapter=adapter)

    result = mapper.map_event(event("Завтра отправлю отчёт. Позвоню клиенту вечером."))

    assert result.failed is True
    assert result.reason == "mapper_failure:RuntimeError"
    assert len(result.written) == 1
    assert result.written[0].payload["semantic_key"] == "commitment:send-report"
    assert len(store.cards) == 1


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


# issue92-final-archive-regressions

def test_edited_zero_candidates_archives_source_and_reports_receipt_change():
    from app_v2.services.operation_receipts import personal_operation_receipts

    store = FakeStore()
    adapter = FakeAdapter(responses=[
        {"candidates": [candidate()]},
        {"candidates": []},
        {"candidates": []},
    ])
    mapper = MemoryMapper(store=store, adapter=adapter)
    original = event("Завтра отправлю отчёт", message_id="m-zero")
    first = mapper.map_event(original)
    memory_id = first.written[0].id
    edited = original.model_copy(update={
        "event_type": EventType.EDITED_MESSAGE,
        "text": "Это сообщение больше не содержит обещания.",
        "occurred_at": NOW + timedelta(minutes=1),
    })

    cleared = mapper.map_event(edited)
    assert cleared.failed is False
    assert cleared.reason == "edited_source_cleared"
    assert cleared.forgotten_ids == (memory_id,)
    assert store.cards[memory_id].status is MemoryStatus.ARCHIVED
    receipt = personal_operation_receipts(cleared)["memory"]
    assert receipt["status"] == "succeeded"
    assert receipt["changed"] is True
    assert receipt["forgotten_ids"] == [memory_id]

    retried = mapper.map_event(edited)
    retry_receipt = personal_operation_receipts(retried)["memory"]
    assert retried.failed is False
    assert retried.forgotten_ids == ()
    assert retry_receipt["changed"] is False


def test_edited_source_retains_unchanged_sibling_and_reports_stale_archive():
    from app_v2.services.operation_receipts import personal_operation_receipts

    store = FakeStore()
    original = "Альфа отправится 20-го. Бета стоит 900 USD."
    adapter = FakeAdapter(responses=[
        {"candidates": [
            candidate(semantic_key="alpha:planned", summary="Альфа запланирована.", evidence_excerpt="Альфа отправится 20-го"),
            candidate(semantic_key="beta:900", summary="Бета стоит 900 USD.", evidence_excerpt="Бета стоит 900 USD"),
        ]},
        {"candidates": [
            candidate(semantic_key="alpha:planned", summary="Альфа запланирована.", evidence_excerpt="Альфа отправится 20-го"),
        ]},
    ])
    mapper = MemoryMapper(store=store, adapter=adapter)
    base = event(original, message_id="m-sibling")
    first = mapper.map_event(base)
    alpha = next(card for card in first.written if card.payload["semantic_key"] == "alpha:planned")
    beta = next(card for card in first.written if card.payload["semantic_key"] == "beta:900")
    edited = base.model_copy(update={
        "event_type": EventType.EDITED_MESSAGE,
        "text": "Альфа отправится 20-го.",
        "occurred_at": NOW + timedelta(minutes=1),
    })

    result = mapper.map_event(edited)
    assert result.failed is False
    assert result.written == ()
    assert result.forgotten_ids == (beta.id,)
    assert store.cards[alpha.id].status is not MemoryStatus.ARCHIVED
    assert store.cards[beta.id].status is MemoryStatus.ARCHIVED
    receipt = personal_operation_receipts(result)["memory"]
    assert receipt["changed"] is True
    assert receipt["written_ids"] == []
    assert receipt["forgotten_ids"] == [beta.id]


def test_edited_zero_candidates_does_not_archive_same_message_id_in_other_scope():
    store = FakeStore()
    adapter = FakeAdapter(responses=[
        {"candidates": [candidate()]},
        {"candidates": [candidate()]},
        {"candidates": []},
    ])
    mapper = MemoryMapper(store=store, adapter=adapter)
    personal = event("Завтра отправлю отчёт", message_id="m-shared", scope=ScopeType.PERSONAL, scope_id="u1")
    group = event("Завтра отправлю отчёт", message_id="m-shared", scope=ScopeType.GROUP, scope_id="g1")
    personal_card = mapper.map_event(personal).written[0]
    group_card = mapper.map_event(group).written[0]
    edited = personal.model_copy(update={
        "event_type": EventType.EDITED_MESSAGE,
        "text": "Обещание удалено.",
        "occurred_at": NOW + timedelta(minutes=1),
    })

    result = mapper.map_event(edited)
    assert result.forgotten_ids == (personal_card.id,)
    assert store.cards[personal_card.id].status is MemoryStatus.ARCHIVED
    assert store.cards[group_card.id].status is not MemoryStatus.ARCHIVED


def test_edited_zero_candidates_partial_archive_failure_reports_durable_ids():
    from app_v2.services.operation_receipts import personal_operation_receipts

    class FailSecondArchiveStore(FakeStore):
        def __init__(self):
            super().__init__()
            self.archive_calls = 0

        def archive(self, scope_type, scope_id, memory_id):
            self.archive_calls += 1
            if self.archive_calls == 2:
                raise RuntimeError("simulated archive failure")
            return super().archive(scope_type, scope_id, memory_id)

    store = FailSecondArchiveStore()
    adapter = FakeAdapter(responses=[
        {"candidates": [
            candidate(semantic_key="alpha:planned", summary="Альфа запланирована.", evidence_excerpt="Альфа отправится 20-го"),
            candidate(semantic_key="beta:900", summary="Бета стоит 900 USD.", evidence_excerpt="Бета стоит 900 USD"),
        ]},
        {"candidates": []},
    ])
    mapper = MemoryMapper(store=store, adapter=adapter)
    base = event("Альфа отправится 20-го. Бета стоит 900 USD.", message_id="m-partial-archive")
    first = mapper.map_event(base)
    edited = base.model_copy(update={
        "event_type": EventType.EDITED_MESSAGE,
        "text": "Старые сведения полностью удалены.",
        "occurred_at": NOW + timedelta(minutes=1),
    })

    result = mapper.map_event(edited)
    assert result.failed is True
    assert result.reason == "mapper_failure:RuntimeError"
    assert len(result.forgotten_ids) == 1
    assert result.forgotten_ids[0] in {card.id for card in first.written}
    receipt = personal_operation_receipts(result)["memory"]
    assert receipt["status"] == "partial"
    assert receipt["changed"] is True
    assert receipt["forgotten_ids"] == list(result.forgotten_ids)


# issue92-correction-safety-final

def test_edit_with_rejected_candidate_fails_without_erasing_memory():
    store = FakeStore()
    adapter = FakeAdapter(responses=[
        {"candidates": [candidate(source_message_id="m-safe", evidence_excerpt="Завтра отправлю отчёт")]},
        {"candidates": [candidate(
            semantic_key="commitment:changed",
            source_message_id="m-safe",
            evidence_excerpt="Фрагмент которого нет в edit",
        )]},
    ])
    mapper = MemoryMapper(store=store, adapter=adapter)
    base = event("Завтра отправлю отчёт", message_id="m-safe")
    first = mapper.map_event(base)
    memory_id = first.written[0].id
    edited = base.model_copy(update={
        "event_type": EventType.EDITED_MESSAGE,
        "text": "Отчёт пока под вопросом",
        "occurred_at": NOW + timedelta(minutes=1),
    })

    result = mapper.map_event(edited)
    assert result.failed is True
    assert result.reason == "edited_source_invalid_candidate"
    assert result.written == ()
    assert result.forgotten_ids == ()
    assert store.cards[memory_id].status is not MemoryStatus.ARCHIVED
    assert store.cards[memory_id].evidence[0].excerpt == "Завтра отправлю отчёт"


def test_shared_card_edit_new_semantic_key_detaches_only_edited_source():
    store = FakeStore()
    adapter = FakeAdapter(responses=[
        {"candidates": [candidate(
            semantic_key="shipment:planned",
            summary="Отправка запланирована.",
            source_message_id="m-shared-a",
            evidence_excerpt="Отправка запланирована",
        )]},
        {"candidates": [candidate(
            semantic_key="shipment:planned",
            summary="Отправка запланирована.",
            source_message_id="m-shared-b",
            evidence_excerpt="Да, отправка запланирована",
        )]},
        {"candidates": [candidate(
            semantic_key="shipment:cancelled",
            summary="Отправка отменена.",
            source_message_id="m-shared-a",
            evidence_excerpt="Отправка отменена",
        )]},
    ])
    mapper = MemoryMapper(store=store, adapter=adapter)
    first_event = event("Отправка запланирована", message_id="m-shared-a")
    second_event = event("Да, отправка запланирована", message_id="m-shared-b", occurred_at=NOW + timedelta(hours=7))
    first = mapper.map_event(first_event)
    mapper.map_event(second_event)
    shared_id = first.written[0].id
    assert store.cards[shared_id].source_count == 2

    edited = first_event.model_copy(update={
        "event_type": EventType.EDITED_MESSAGE,
        "text": "Отправка отменена",
        "occurred_at": NOW + timedelta(hours=8),
    })
    result = mapper.map_event(edited)
    assert result.failed is False

    old_card = store.cards[shared_id]
    assert old_card.status is not MemoryStatus.ARCHIVED
    assert old_card.payload["semantic_key"] == "shipment:planned"
    assert old_card.summary == "Отправка запланирована."
    assert old_card.source_count == 1
    assert [item.message_id for item in old_card.evidence] == ["m-shared-b"]

    new_cards = [
        card for card in store.cards.values()
        if card.status is not MemoryStatus.ARCHIVED
        and card.payload.get("semantic_key") == "shipment:cancelled"
    ]
    assert len(new_cards) == 1
    assert new_cards[0].source_count == 1
    assert [item.message_id for item in new_cards[0].evidence] == ["m-shared-a"]
    assert {card.id for card in result.written} == {old_card.id, new_cards[0].id}


def test_zero_candidate_edit_detaches_source_from_shared_card_instead_of_archiving_claim():
    store = FakeStore()
    adapter = FakeAdapter(responses=[
        {"candidates": [candidate(
            semantic_key="shipment:planned",
            source_message_id="m-zero-a",
            evidence_excerpt="Отправка запланирована",
        )]},
        {"candidates": [candidate(
            semantic_key="shipment:planned",
            source_message_id="m-zero-b",
            evidence_excerpt="Да, отправка запланирована",
        )]},
        {"candidates": []},
    ])
    mapper = MemoryMapper(store=store, adapter=adapter)
    first_event = event("Отправка запланирована", message_id="m-zero-a")
    second_event = event("Да, отправка запланирована", message_id="m-zero-b", occurred_at=NOW + timedelta(hours=7))
    shared_id = mapper.map_event(first_event).written[0].id
    mapper.map_event(second_event)

    edited = first_event.model_copy(update={
        "event_type": EventType.EDITED_MESSAGE,
        "text": "Этой информации больше нет",
        "occurred_at": NOW + timedelta(hours=8),
    })
    result = mapper.map_event(edited)
    assert result.failed is False
    assert result.forgotten_ids == ()
    assert [card.id for card in result.written] == [shared_id]
    old_card = store.cards[shared_id]
    assert old_card.status is not MemoryStatus.ARCHIVED
    assert old_card.source_count == 1
    assert [item.message_id for item in old_card.evidence] == ["m-zero-b"]


def test_weaker_single_source_edit_demotes_active_card_and_does_not_raise_confidence():
    store = FakeStore()
    strong = candidate(
        memory_type="commitment",
        semantic_key="decision:report",
        summary="Отчёт точно будет завтра.",
        source_message_id="m-demote",
        evidence_excerpt="Отчёт точно будет завтра",
        confidence=0.95,
    )
    weak = candidate(
        memory_type="observation",
        semantic_key="decision:report",
        summary="Отчёт, возможно, будет позже.",
        source_message_id="m-demote",
        evidence_excerpt="Отчёт, возможно, будет позже",
        confidence=0.3,
        usage_policy={"assist": True, "callback": False, "roast": False, "proactive": False},
    )
    adapter = FakeAdapter(responses=[{"candidates": [strong]}, {"candidates": [weak]}])
    mapper = MemoryMapper(store=store, adapter=adapter)
    base = event("Отчёт точно будет завтра", message_id="m-demote")
    first = mapper.map_event(base).written[0]
    assert first.status is MemoryStatus.ACTIVE

    edited = base.model_copy(update={
        "event_type": EventType.EDITED_MESSAGE,
        "text": "Отчёт, возможно, будет позже",
        "occurred_at": NOW + timedelta(minutes=2),
    })
    corrected = mapper.map_event(edited).written[-1]
    assert corrected.id == first.id
    assert corrected.status is MemoryStatus.CANDIDATE
    assert corrected.memory_type == "observation"
    assert corrected.confidence <= first.confidence


# issue92-pre-mutation-matching-final

def test_ambiguous_edited_source_fails_before_any_memory_mutation():
    store = FakeStore()
    adapter = FakeAdapter(responses=[
        {"candidates": [
            candidate(semantic_key="alpha:old", summary="Альфа старая.", evidence_excerpt="Альфа старая"),
            candidate(semantic_key="beta:old", summary="Бета старая.", evidence_excerpt="Бета старая"),
        ]},
        {"candidates": [
            candidate(semantic_key="alpha:new", summary="Альфа новая.", evidence_excerpt="Альфа новая"),
            candidate(semantic_key="beta:new", summary="Бета новая.", evidence_excerpt="Бета новая"),
        ]},
    ])
    mapper = MemoryMapper(store=store, adapter=adapter)
    base = event("Альфа старая. Бета старая.", message_id="m-ambiguous")
    first = mapper.map_event(base)
    assert len(first.written) == 2

    snapshots = {}
    for card in first.written:
        payload = dict(card.payload)
        payload.pop("source_candidate_index", None)
        legacy = card.model_copy(update={"payload": payload})
        store.cards[card.id] = legacy
        snapshots[card.id] = legacy

    edited = base.model_copy(update={
        "event_type": EventType.EDITED_MESSAGE,
        "text": "Альфа новая. Бета новая.",
        "occurred_at": NOW + timedelta(minutes=1),
    })
    result = mapper.map_event(edited)

    assert result.failed is True
    assert result.reason == "edited_source_reconciliation_ambiguous"
    assert result.written == ()
    assert result.forgotten_ids == ()
    assert store.cards == snapshots
    assert all(card.status is not MemoryStatus.ARCHIVED for card in store.cards.values())


def test_edit_retaining_later_candidate_matches_semantic_identity_not_renumbered_slot():
    from app_v2.services.operation_receipts import personal_operation_receipts

    store = FakeStore()
    adapter = FakeAdapter(responses=[
        {"candidates": [
            candidate(semantic_key="alpha:planned", summary="Альфа запланирована.", evidence_excerpt="Альфа отправится 20-го"),
            candidate(semantic_key="beta:900", summary="Бета стоит 900 USD.", evidence_excerpt="Бета стоит 900 USD"),
        ]},
        {"candidates": [
            candidate(semantic_key="beta:900", summary="Бета стоит 900 USD.", evidence_excerpt="Бета стоит 900 USD"),
        ]},
    ])
    mapper = MemoryMapper(store=store, adapter=adapter)
    base = event("Альфа отправится 20-го. Бета стоит 900 USD.", message_id="m-retain-beta")
    first = mapper.map_event(base)
    alpha = next(card for card in first.written if card.payload["semantic_key"] == "alpha:planned")
    beta = next(card for card in first.written if card.payload["semantic_key"] == "beta:900")

    edited = base.model_copy(update={
        "event_type": EventType.EDITED_MESSAGE,
        "text": "Бета стоит 900 USD.",
        "occurred_at": NOW + timedelta(minutes=1),
    })
    result = mapper.map_event(edited)

    assert result.failed is False
    assert result.written == ()
    assert result.forgotten_ids == (alpha.id,)
    assert store.cards[alpha.id].status is MemoryStatus.ARCHIVED
    assert store.cards[beta.id].status is not MemoryStatus.ARCHIVED
    assert store.cards[beta.id].payload["semantic_key"] == "beta:900"
    assert store.cards[beta.id].summary == "Бета стоит 900 USD."
    assert store.cards[beta.id].evidence[0].excerpt == "Бета стоит 900 USD"
    receipt = personal_operation_receipts(result)["memory"]
    assert receipt["changed"] is True
    assert receipt["forgotten_ids"] == [alpha.id]


def test_removed_plus_identity_changed_candidate_fails_closed_without_guessing():
    store = FakeStore()
    adapter = FakeAdapter(responses=[
        {"candidates": [
            candidate(semantic_key="alpha:old", summary="Альфа старая.", evidence_excerpt="Альфа старая"),
            candidate(semantic_key="beta:old", summary="Бета старая.", evidence_excerpt="Бета старая"),
        ]},
        {"candidates": [
            candidate(semantic_key="beta:new", summary="Бета новая.", evidence_excerpt="Бета новая"),
        ]},
    ])
    mapper = MemoryMapper(store=store, adapter=adapter)
    base = event("Альфа старая. Бета старая.", message_id="m-remove-change")
    first = mapper.map_event(base)
    before = dict(store.cards)
    edited = base.model_copy(update={
        "event_type": EventType.EDITED_MESSAGE,
        "text": "Бета новая.",
        "occurred_at": NOW + timedelta(minutes=1),
    })

    result = mapper.map_event(edited)
    assert result.failed is True
    assert result.reason == "edited_source_reconciliation_ambiguous"
    assert result.written == ()
    assert result.forgotten_ids == ()
    assert store.cards == before
    assert {card.id for card in first.written} == set(store.cards)


def test_edited_source_merges_into_existing_semantic_card_and_archives_old_card():
    from app_v2.services.operation_receipts import personal_operation_receipts

    store = FakeStore()
    mapper = MemoryMapper(store=store, adapter=FakeAdapter(responses=[
        {"candidates": [candidate(
            semantic_key="claim:a",
            summary="Синтетический проект использует вариант A.",
            evidence_excerpt="Проект использует вариант A",
        )]},
        {"candidates": [candidate(
            semantic_key="claim:b",
            summary="Синтетический проект использует вариант B.",
            evidence_excerpt="Проект использует вариант B",
        )]},
        {"candidates": [candidate(
            semantic_key="claim:b",
            summary="Синтетический проект использует вариант B.",
            evidence_excerpt="Теперь проект использует вариант B",
        )]},
        {"candidates": [candidate(
            semantic_key="claim:b",
            summary="Синтетический проект использует вариант B.",
            evidence_excerpt="Теперь проект использует вариант B",
        )]},
    ]))
    source_a = event("Проект использует вариант A", message_id="source-a")
    source_b = event(
        "Проект использует вариант B",
        message_id="source-b",
        occurred_at=NOW + timedelta(hours=7),
    )
    old_a = mapper.map_event(source_a).written[0]
    existing_b = mapper.map_event(source_b).written[0]
    before_retry = store.cards[existing_b.id]

    edited = source_a.model_copy(update={
        "event_type": EventType.EDITED_MESSAGE,
        "text": "Теперь проект использует вариант B",
        "occurred_at": NOW + timedelta(minutes=5),
    })
    corrected = mapper.map_event(edited)

    assert corrected.failed is False
    assert corrected.forgotten_ids == (old_a.id,)
    assert len(corrected.written) == 1
    merged = corrected.written[0]
    assert merged.id == existing_b.id
    assert store.cards[old_a.id].status is MemoryStatus.ARCHIVED
    assert {item.message_id for item in merged.evidence} == {"source-a", "source-b"}
    assert merged.source_count == 2
    assert merged.payload["episode_count"] == 2
    receipt = personal_operation_receipts(corrected)["memory"]
    assert receipt["changed"] is True
    assert receipt["written_ids"] == [existing_b.id]
    assert receipt["forgotten_ids"] == [old_a.id]

    retried = mapper.map_event(edited)
    assert retried.written == ()
    assert retried.forgotten_ids == ()
    assert personal_operation_receipts(retried)["memory"]["changed"] is False
    assert store.cards[existing_b.id] == merged
    assert merged.confidence >= before_retry.confidence



def test_same_source_retry_is_noop_when_model_classification_drifts():
    store = FakeStore()
    source = event("Завтра отправлю отчёт", message_id="m-classification-retry")
    adapter = FakeAdapter(responses=[
        {"candidates": [candidate(
            memory_type="commitment",
            semantic_key="commitment:send-report",
            source_message_id="m-classification-retry",
            evidence_excerpt="Завтра отправлю отчёт",
        )]},
        {"candidates": [candidate(
            memory_type="observation",
            semantic_key="observation:different-model-key",
            source_message_id="m-classification-retry",
            evidence_excerpt="Завтра отправлю отчёт",
            confidence=0.4,
        )]},
    ])
    mapper = MemoryMapper(store=store, adapter=adapter)

    first = mapper.map_event(source)
    persisted_before = first.written[0]
    second = mapper.map_event(source)

    assert second.written == ()
    assert len(store.cards) == 1
    assert store.cards[persisted_before.id] == persisted_before
    assert store.create_calls == 1
    assert store.update_calls == 0


def test_group_person_specific_same_semantic_key_stays_separate_by_author():
    store = FakeStore()
    adapter = FakeAdapter(responses=[
        {"candidates": [candidate(
            semantic_key="commitment:send-report",
            subject_keys=["user:u1"],
            source_message_id="g-msg-1",
            evidence_excerpt="Завтра отправлю отчёт",
        )]},
        {"candidates": [candidate(
            semantic_key="commitment:send-report",
            subject_keys=["user:u2"],
            source_message_id="g-msg-2",
            evidence_excerpt="Завтра отправлю отчёт",
        )]},
    ])
    mapper = MemoryMapper(store=store, adapter=adapter)
    first_event = event(
        "Завтра отправлю отчёт",
        scope=ScopeType.GROUP,
        scope_id="g-authors",
        message_id="g-msg-1",
        actor_user_id="u1",
    )
    second_event = event(
        "Завтра отправлю отчёт",
        scope=ScopeType.GROUP,
        scope_id="g-authors",
        message_id="g-msg-2",
        actor_user_id="u2",
    )

    first = mapper.map_event(first_event)
    second = mapper.map_event(second_event)

    assert len(store.cards) == 2
    assert first.written[0].id != second.written[0].id
    by_author = {
        card.evidence[0].author_id: card
        for card in store.cards.values()
    }
    assert set(by_author) == {"u1", "u2"}
    assert by_author["u1"].subject_keys == ["user:u1"]
    assert by_author["u2"].subject_keys == ["user:u2"]
    assert all(card.source_count == 1 for card in store.cards.values())
