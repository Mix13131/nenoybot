from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app_v2.domain.enums import EventType, MemoryOrigin, MemoryStatus, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.domain.memory import MemoryCard, MemoryEvidence, UsagePolicy
from app_v2.services.statement_watcher import StatementWatcher


class FakeAdapter:
    def __init__(self, parsed=None, error=None):
        self.parsed = parsed or {}
        self.error = error
        self.calls = []

    def generate_json(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        return SimpleNamespace(parsed=self.parsed)


def event(text="не, завтра не поеду") -> EventEnvelope:
    return EventEnvelope(
        event_id="e2",
        event_type=EventType.GROUP_MESSAGE,
        occurred_at=datetime.now(timezone.utc),
        scope_type=ScopeType.GROUP,
        scope_id="-100777",
        actor_user_id="123",
        message_id="11",
        text=text,
    )


def card(*, memory_id="c1", memory_type="commitment", scope_id="-100777") -> MemoryCard:
    now = datetime.now(timezone.utc)
    return MemoryCard(
        id=memory_id,
        scope_type=ScopeType.GROUP,
        scope_id=scope_id,
        memory_type=memory_type,
        subject_keys=["user:123"],
        summary="Сергей сказал, что завтра идёт в баню.",
        payload={"statement_kind": "commitment", "status": "open", "verbatim": "завтра баня, я буду"},
        importance=.9,
        confidence=.86,
        freshness=1.0,
        status=MemoryStatus.ACTIVE,
        origin=MemoryOrigin.INFERRED,
        usage_policy=UsagePolicy(assist=True, callback=True, roast=True, proactive=True),
        evidence=[
            MemoryEvidence(
                message_id="10",
                author_id="123",
                timestamp=now,
                excerpt="Завтра баня, я буду.",
            )
        ],
        source_count=1,
        created_at=now,
        updated_at=now,
    )


def test_watcher_selects_grounded_broken_commitment() -> None:
    adapter = FakeAdapter({
        "candidate_index": 0,
        "relation": "broken_commitment",
        "confidence": .94,
        "roast_fit": .90,
    })
    result = StatementWatcher(adapter).evaluate(event=event(), candidates=[card()])

    assert result.relation == "broken_commitment"
    assert result.strong_mismatch is True
    assert result.memory_id == "c1"
    assert result.evidence_excerpt == "Завтра баня, я буду."
    assert len(adapter.calls) == 1


def test_watcher_rejects_low_confidence_relation() -> None:
    adapter = FakeAdapter({
        "candidate_index": 0,
        "relation": "contradiction",
        "confidence": .50,
        "roast_fit": .90,
    })
    result = StatementWatcher(adapter).evaluate(event=event(), candidates=[card()])

    assert result.relation == "none"
    assert result.memory_id is None


def test_watcher_ignores_candidate_without_evidence_or_wrong_scope() -> None:
    no_evidence = card().model_copy(update={"evidence": []})
    wrong_scope = card(memory_id="c2", scope_id="-999")
    adapter = FakeAdapter(error=AssertionError("model must not be called"))

    result = StatementWatcher(adapter).evaluate(event=event(), candidates=[no_evidence, wrong_scope])

    assert result.relation == "none"
    assert adapter.calls == []


def test_watcher_failure_is_safe_silence() -> None:
    adapter = FakeAdapter(error=RuntimeError("classifier down"))
    result = StatementWatcher(adapter).evaluate(event=event(), candidates=[card()])

    assert result.relation == "none"
    assert result.strong_mismatch is False
