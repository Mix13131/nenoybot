from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app_v2.domain.actions import ActionRequest
from app_v2.domain.feedback import FeedbackEvent
from app_v2.domain.outbound import OutboundMessage
from app_v2.domain.usage import LLMUsageRecord


NOW = datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc)


def test_action_request_and_outbound_message_validate() -> None:
    action = ActionRequest(
        action_type="create_reminder",
        scope_type="personal",
        scope_id="user_1",
        actor_user_id="user_1",
        requested_at=NOW,
    )
    outbound = OutboundMessage(
        message_id="out_1",
        scope_type="personal",
        scope_id="user_1",
        text="Напомню.",
        dedupe_key="reply:event_1",
    )
    assert action.scope_id == outbound.scope_id


def test_feedback_datetime_is_timezone_aware() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        FeedbackEvent(
            feedback_id="fb_1",
            scope_type="group",
            scope_id="chat_1",
            feedback_type="reaction",
            occurred_at=datetime(2026, 9, 14, 8, 0),
        )


def test_usage_rejects_negative_numbers() -> None:
    base = {
        "usage_id": "usage_1",
        "task_kind": "scene_analysis",
        "model": "test-model",
        "input_tokens": 10,
        "output_tokens": 5,
        "estimated_cost_usd": 0.01,
        "latency_ms": 100,
        "success": True,
        "created_at": NOW,
    }
    for field, value in [
        ("input_tokens", -1),
        ("output_tokens", -1),
        ("estimated_cost_usd", -0.1),
        ("latency_ms", -1),
    ]:
        payload = {**base, field: value}
        with pytest.raises(ValidationError):
            LLMUsageRecord(**payload)


def test_usage_record_accepts_valid_values() -> None:
    record = LLMUsageRecord(
        usage_id="usage_1",
        task_kind="generation",
        model="test-model",
        input_tokens=10,
        output_tokens=5,
        cached_tokens=0,
        estimated_cost_usd=0.01,
        latency_ms=120,
        success=True,
        created_at=NOW,
    )
    assert record.input_tokens == 10
