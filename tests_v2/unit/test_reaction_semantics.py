from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.services.feedback_collector import _reaction_type


NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


def event(new_reaction, *, old_reaction=None, event_type=EventType.REACTION_ADDED):
    return EventEnvelope(
        event_id="tg:reaction",
        event_type=event_type,
        occurred_at=NOW,
        scope_type=ScopeType.GROUP,
        scope_id="-100777",
        actor_user_id="123",
        message_id="88",
        metadata={
            "old_reaction": old_reaction or [],
            "new_reaction": new_reaction,
        },
    )


@pytest.mark.parametrize("emoji", ["😁", "😆", "😂", "🤣", "😅"])
def test_laughter_reactions_are_positive_entertainment(emoji: str) -> None:
    feedback_type, value, payload = _reaction_type(
        event([{"type": "emoji", "emoji": emoji}])
    )
    assert feedback_type == "reaction_positive"
    assert value == 1.0
    assert payload["reaction_identities"] == [f"emoji:{emoji}"]
    assert payload["reaction_families"] == ["laughter_entertainment"]
    assert payload["quality_valence"] == "positive"


@pytest.mark.parametrize(
    ("emoji", "family"),
    [
        ("❤️", "affection"),
        ("👍", "approval_support"),
        ("🤔", "curiosity_surprise"),
        ("👀", "curiosity_surprise"),
        ("🤡", "ambiguous_playful"),
        ("💩", "ambiguous_playful"),
    ],
)
def test_reaction_families_are_preserved_without_overclaiming(
    emoji: str,
    family: str,
) -> None:
    feedback_type, _, payload = _reaction_type(
        event([{"type": "emoji", "emoji": emoji}])
    )
    assert payload["reaction_identities"] == [f"emoji:{emoji}"]
    assert payload["reaction_families"] == [family]
    if family in {"curiosity_surprise", "ambiguous_playful"}:
        assert feedback_type == "reaction_neutral"
        assert payload["quality_valence"] == "unknown"


def test_explicit_negative_reaction_is_high_confidence_negative() -> None:
    feedback_type, value, payload = _reaction_type(
        event([{"type": "emoji", "emoji": "👎"}])
    )
    assert feedback_type == "reaction_negative"
    assert value == -1.0
    assert payload["reaction_families"] == ["explicit_negative"]
    assert payload["quality_valence"] == "negative"


def test_unknown_unicode_is_kept_as_engagement() -> None:
    feedback_type, value, payload = _reaction_type(
        event([{"type": "emoji", "emoji": "🪿"}])
    )
    assert feedback_type == "reaction_neutral"
    assert value == 0.0
    assert payload["reaction_identities"] == ["emoji:🪿"]
    assert payload["reaction_families"] == ["custom_unknown"]
    assert payload["reaction_engaged"] is True


def test_custom_emoji_id_is_not_lost() -> None:
    feedback_type, value, payload = _reaction_type(
        event([{"type": "custom_emoji", "custom_emoji_id": "987654321"}])
    )
    assert feedback_type == "reaction_neutral"
    assert value == 0.0
    assert payload["reaction_identities"] == ["custom:987654321"]
    assert payload["reaction_families"] == ["custom_unknown"]


def test_future_paid_reaction_type_is_observable_without_sentiment_guess() -> None:
    feedback_type, _, payload = _reaction_type(event([{"type": "paid"}]))
    assert feedback_type == "reaction_neutral"
    assert payload["reaction_identities"] == ["type:paid"]
    assert payload["reaction_families"] == ["custom_unknown"]


def test_mixed_positive_and_negative_state_is_not_two_quality_votes() -> None:
    feedback_type, value, payload = _reaction_type(
        event(
            [
                {"type": "emoji", "emoji": "😂"},
                {"type": "emoji", "emoji": "👎"},
            ]
        )
    )
    assert feedback_type == "reaction_neutral"
    assert value == 0.0
    assert payload["quality_valence"] == "mixed_unknown"
    assert set(payload["reaction_families"]) == {
        "laughter_entertainment",
        "explicit_negative",
    }


def test_removed_reaction_is_empty_current_state() -> None:
    feedback_type, value, payload = _reaction_type(
        event(
            [],
            old_reaction=[{"type": "emoji", "emoji": "👍"}],
            event_type=EventType.REACTION_REMOVED,
        )
    )
    assert feedback_type == "reaction_removed"
    assert value == 0.0
    assert payload["reaction_engaged"] is False
    assert payload["old_emoji"] == ["👍"]
