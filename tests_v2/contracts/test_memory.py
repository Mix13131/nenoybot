from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app_v2.domain.memory import MemoryCard, MemoryRelation


NOW = datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc)


def make_card(**overrides):
    payload = {
        "id": "mem_1",
        "scope_type": "group",
        "scope_id": "chat_1",
        "memory_type": "running_joke",
        "summary": "Серёга говорит «уже еду» до выезда.",
        "importance": 0.7,
        "confidence": 0.9,
        "freshness": 0.8,
        "origin": "inferred",
        "created_at": NOW,
        "updated_at": NOW,
    }
    payload.update(overrides)
    return MemoryCard(**payload)


def test_memory_card_accepts_valid_group_scope() -> None:
    card = make_card()
    assert card.scope_id == "chat_1"


def test_group_memory_empty_scope_id_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_card(scope_id="   ")


def test_memory_quality_is_bounded() -> None:
    with pytest.raises(ValidationError):
        make_card(confidence=1.01)
    with pytest.raises(ValidationError):
        make_card(freshness=-0.01)


def test_memory_datetime_must_be_timezone_aware() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        make_card(created_at=datetime(2026, 9, 14, 8, 0))


def test_memory_relation_weight_is_bounded() -> None:
    with pytest.raises(ValidationError):
        MemoryRelation(
            id="rel_1",
            scope_type="group",
            scope_id="chat_1",
            from_memory_id="mem_1",
            relation_type="supports",
            to_memory_id="mem_2",
            weight=1.2,
        )
