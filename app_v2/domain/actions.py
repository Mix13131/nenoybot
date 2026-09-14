from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, field_validator

from .base import DomainModel, NonEmptyStr, ensure_timezone_aware
from .enums import ScopeType


class ActionRequest(DomainModel):
    action_type: NonEmptyStr
    scope_type: ScopeType
    scope_id: NonEmptyStr
    actor_user_id: str | None = None
    target_user_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    requested_at: datetime

    @field_validator("requested_at")
    @classmethod
    def validate_requested_at(cls, value: datetime) -> datetime:
        return ensure_timezone_aware(value, "requested_at")
