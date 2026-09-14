from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, field_validator

from .base import DomainModel, NonEmptyStr, ensure_timezone_aware
from .enums import ScopeType


class FeedbackEvent(DomainModel):
    feedback_id: NonEmptyStr
    scope_type: ScopeType
    scope_id: NonEmptyStr
    user_id: str | None = None
    intervention_id: str | None = None
    feedback_type: NonEmptyStr
    value: float | str | bool | None = None
    occurred_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: datetime) -> datetime:
        return ensure_timezone_aware(value, "occurred_at")
