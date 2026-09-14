from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, field_validator

from .base import DomainModel, NonEmptyStr, Score, ensure_timezone_aware
from .enums import EventType, ScopeType


class EventEnvelope(DomainModel):
    event_id: NonEmptyStr
    event_type: EventType
    occurred_at: datetime
    scope_type: ScopeType
    scope_id: NonEmptyStr
    actor_user_id: str | None = None
    message_id: str | None = None
    reply_to_message_id: str | None = None
    text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: datetime) -> datetime:
        return ensure_timezone_aware(value, "occurred_at")


class SceneAnalysis(DomainModel):
    direct_mention: bool = False
    reply_to_bot: bool = False
    question_to_bot: bool = False
    command_intent: str | None = None
    banter_score: Score = 0.0
    seriousness_score: Score = 0.0
    conflict_score: Score = 0.0
    sensitivity_score: Score = 0.0
    roast_opportunity: Score = 0.0
    callback_opportunity: Score = 0.0
    help_opportunity: Score = 0.0
    memory_value: Score = 0.0
    contradiction_score: Score = 0.0
    commitment_signal: Score = 0.0
    decision_signal: Score = 0.0
