import pytest
from pydantic import ValidationError

from app_v2.domain.decisions import DispatcherDecision


def test_reply_requires_mode() -> None:
    with pytest.raises(ValidationError, match="mode is required"):
        DispatcherDecision(primary_action="reply")


def test_reply_with_mode_is_valid() -> None:
    decision = DispatcherDecision(
        primary_action="reply",
        mode="group_roast",
        intervention_score=82,
        reason_codes=["roast_opportunity"],
    )
    assert decision.mode.value == "group_roast"


def test_ignore_does_not_require_mode() -> None:
    decision = DispatcherDecision(primary_action="ignore")
    assert decision.mode is None


def test_invalid_primary_action_is_rejected() -> None:
    with pytest.raises(ValidationError):
        DispatcherDecision(primary_action="explode")


def test_intervention_score_is_bounded() -> None:
    with pytest.raises(ValidationError):
        DispatcherDecision(primary_action="ignore", intervention_score=101)
