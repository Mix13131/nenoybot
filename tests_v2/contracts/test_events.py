from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app_v2.domain.events import EventEnvelope, SceneAnalysis


def valid_event() -> EventEnvelope:
    return EventEnvelope(
        event_id="evt_1",
        event_type="group_message",
        occurred_at=datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc),
        scope_type="group",
        scope_id="chat_1",
        actor_user_id="user_1",
        message_id="msg_1",
        text="Уже еду",
    )


def test_event_round_trip() -> None:
    event = valid_event()
    restored = EventEnvelope.model_validate(event.model_dump())
    assert restored == event


def test_invalid_scope_is_rejected() -> None:
    payload = valid_event().model_dump()
    payload["scope_type"] = "planet"
    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(payload)


def test_invalid_event_type_is_rejected() -> None:
    payload = valid_event().model_dump()
    payload["event_type"] = "something_else"
    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(payload)


def test_naive_event_datetime_is_rejected() -> None:
    payload = valid_event().model_dump()
    payload["occurred_at"] = datetime(2026, 9, 14, 8, 0)
    with pytest.raises(ValidationError, match="timezone-aware"):
        EventEnvelope.model_validate(payload)


def test_scene_score_outside_unit_interval_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SceneAnalysis(roast_opportunity=1.1)
    with pytest.raises(ValidationError):
        SceneAnalysis(conflict_score=-0.1)


def test_extra_event_field_is_rejected() -> None:
    payload = valid_event().model_dump()
    payload["telegram_update"] = {}
    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(payload)
