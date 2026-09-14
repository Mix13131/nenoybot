from __future__ import annotations

from datetime import datetime, timezone

from app_v2.domain.enums import MemoryOrigin, MemoryStatus, ScopeType
from app_v2.domain.memory import MemoryCard, UsagePolicy
from app_v2.repositories.memory_repo import RankedMemory
from app_v2.services.retrieval_engine import RetrievalEngine


def _card(memory_id: str, scope_type: ScopeType, scope_id: str, score: float) -> RankedMemory:
    now = datetime.now(timezone.utc)
    card = MemoryCard(
        id=memory_id,
        scope_type=scope_type,
        scope_id=scope_id,
        memory_type="observation",
        subject_keys=["user:u1"],
        summary=f"memory {memory_id}",
        importance=0.8,
        confidence=0.9,
        freshness=0.9,
        status=MemoryStatus.ACTIVE,
        origin=MemoryOrigin.EXPLICIT,
        usage_policy=UsagePolicy(assist=True, callback=True, roast=False, proactive=False),
        created_at=now,
        updated_at=now,
    )
    return RankedMemory(card, score)


class FakeRepo:
    def __init__(self) -> None:
        self.calls = []
        self.personal = [_card("personal-1", ScopeType.PERSONAL, "u1", 0.9)]
        self.group = [_card("group-1", ScopeType.GROUP, "g1", 0.8)]
        self.related = [_card("group-related", ScopeType.GROUP, "g1", 0.7)]

    def retrieve_candidates(self, scope_type, scope_id, **kwargs):
        self.calls.append(("direct", scope_type, scope_id, kwargs))
        if scope_type is ScopeType.GROUP and scope_id == "g1":
            return list(self.group)
        if scope_type is ScopeType.PERSONAL and scope_id == "u1":
            return list(self.personal)
        return []

    def related_cards(self, scope_type, scope_id, seed_ids, **kwargs):
        self.calls.append(("related", scope_type, scope_id, kwargs))
        if scope_type is ScopeType.GROUP and scope_id == "g1":
            return list(self.related)
        return []


def test_privacy_regression_group_retrieval_never_falls_back_to_personal() -> None:
    repo = FakeRepo()
    engine = RetrievalEngine(repo)

    result = engine.retrieve(
        ScopeType.GROUP,
        "g1",
        usage="assist",
        subject_keys=["user:u1"],
        limit=8,
    )

    ids = {item.card.id for item in result}
    assert "group-1" in ids
    assert "group-related" in ids
    assert "personal-1" not in ids
    assert all(item.card.scope_type is ScopeType.GROUP for item in result)
    assert all(call[1] is ScopeType.GROUP and call[2] == "g1" for call in repo.calls)


def test_callback_retrieval_passes_fatigue_window() -> None:
    repo = FakeRepo()
    engine = RetrievalEngine(repo)

    engine.retrieve(
        ScopeType.GROUP,
        "g1",
        usage="callback",
        callback_fatigue_minutes=180,
        expand_relations=False,
    )

    assert repo.calls[0][3]["callback_fatigue_minutes"] == 180


def test_non_callback_retrieval_does_not_apply_callback_fatigue() -> None:
    repo = FakeRepo()
    engine = RetrievalEngine(repo)

    engine.retrieve(ScopeType.GROUP, "g1", usage="assist", expand_relations=False)

    assert repo.calls[0][3]["callback_fatigue_minutes"] is None


def test_relation_expansion_deduplicates_and_ranks() -> None:
    repo = FakeRepo()
    repo.related = [
        _card("group-1", ScopeType.GROUP, "g1", 0.95),
        _card("group-related", ScopeType.GROUP, "g1", 0.7),
    ]
    engine = RetrievalEngine(repo)

    result = engine.retrieve(ScopeType.GROUP, "g1", limit=2)

    assert [item.card.id for item in result] == ["group-1", "group-related"]
    assert result[0].score == 0.95


def test_empty_scope_is_rejected() -> None:
    engine = RetrievalEngine(FakeRepo())
    try:
        engine.retrieve(ScopeType.GROUP, "   ")
    except ValueError as exc:
        assert "scope_id" in str(exc)
    else:
        raise AssertionError("empty scope must be rejected")
