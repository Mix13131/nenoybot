from __future__ import annotations

from datetime import datetime

from pydantic import field_validator

from .base import (
    DomainModel,
    NonEmptyStr,
    NonNegativeFloat,
    NonNegativeInt,
    ensure_timezone_aware,
)


class LLMUsageRecord(DomainModel):
    usage_id: NonEmptyStr
    event_id: str | None = None
    intervention_id: str | None = None
    task_kind: NonEmptyStr
    model: NonEmptyStr
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    cached_tokens: NonNegativeInt | None = None
    estimated_cost_usd: NonNegativeFloat
    latency_ms: NonNegativeInt
    success: bool
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        return ensure_timezone_aware(value, "created_at")
