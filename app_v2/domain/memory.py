from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, field_validator

from .base import DomainModel, NonEmptyStr, NonNegativeInt, Score, ensure_timezone_aware
from .enums import MemoryOrigin, MemoryStatus, ScopeType


class MemoryEvidence(DomainModel):
    message_id: str | None = None
    author_id: str | None = None
    timestamp: datetime
    excerpt: NonEmptyStr

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return ensure_timezone_aware(value, "timestamp")


class UsagePolicy(DomainModel):
    assist: bool = True
    callback: bool = True
    roast: bool = False
    proactive: bool = False


class MemoryCard(DomainModel):
    id: NonEmptyStr
    scope_type: ScopeType
    scope_id: NonEmptyStr
    memory_type: NonEmptyStr
    subject_keys: list[str] = Field(default_factory=list)
    summary: NonEmptyStr
    payload: dict[str, Any] = Field(default_factory=dict)
    importance: Score
    confidence: Score
    freshness: Score
    status: MemoryStatus = MemoryStatus.CANDIDATE
    origin: MemoryOrigin
    pinned: bool = False
    usage_policy: UsagePolicy = Field(default_factory=UsagePolicy)
    evidence: list[MemoryEvidence] = Field(default_factory=list)
    source_count: NonNegativeInt = 0
    created_at: datetime
    updated_at: datetime
    last_confirmed_at: datetime | None = None
    last_used_at: datetime | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    superseded_by: str | None = None

    @field_validator(
        "created_at",
        "updated_at",
        "last_confirmed_at",
        "last_used_at",
        "valid_from",
        "valid_until",
    )
    @classmethod
    def validate_datetimes(cls, value: datetime | None, info) -> datetime | None:
        if value is None:
            return None
        return ensure_timezone_aware(value, info.field_name)


class MemoryRelation(DomainModel):
    id: NonEmptyStr
    scope_type: ScopeType
    scope_id: NonEmptyStr
    from_memory_id: NonEmptyStr
    relation_type: NonEmptyStr
    to_memory_id: NonEmptyStr
    weight: Score = 1.0
