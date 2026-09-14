import pytest
from pydantic import ValidationError

from app_v2.domain.personality import PersonalityState


BASE = {
    "mode": "coach",
    "directness": 8,
    "brevity": 8,
    "warmth": 7,
    "pressure": 6,
    "humor": 5,
    "sarcasm": 4,
    "roast": 3,
    "profanity_level": 4,
    "profanity_frequency": 3,
    "initiative": 7,
    "callback": 8,
    "challenge": 8,
    "care": 8,
    "playfulness": 5,
    "sensitivity": 8,
}


def test_personality_state_accepts_valid_values() -> None:
    state = PersonalityState(**BASE)
    assert state.directness == 8


def test_personality_state_rejects_value_above_ten() -> None:
    payload = {**BASE, "roast": 11}
    with pytest.raises(ValidationError):
        PersonalityState(**payload)


def test_personality_state_rejects_negative_value() -> None:
    payload = {**BASE, "warmth": -1}
    with pytest.raises(ValidationError):
        PersonalityState(**payload)
