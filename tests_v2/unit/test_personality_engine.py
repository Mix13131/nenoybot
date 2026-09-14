from __future__ import annotations

from app_v2.domain.enums import ResponseMode, ScopeType
from app_v2.domain.events import SceneAnalysis
from app_v2.services.personality_engine import PersonalityEngine


def test_personal_default_profile():
    state = PersonalityEngine().build(scope_type=ScopeType.PERSONAL, mode=ResponseMode.ASSISTANT)
    assert state.directness == 8
    assert state.warmth == 7
    assert state.profanity_level == 4
    assert state.profanity_frequency == 3


def test_group_default_profile():
    state = PersonalityEngine().build(scope_type=ScopeType.GROUP, mode=ResponseMode.GROUP_BANTER)
    assert state.directness == 9
    assert state.roast == 9
    assert state.profanity_level == 8
    assert state.profanity_frequency == 5


def test_coach_mode_increases_pressure_and_challenge():
    state = PersonalityEngine().build(scope_type=ScopeType.PERSONAL, mode=ResponseMode.COACH)
    assert state.pressure == 8
    assert state.challenge == 10
    assert state.care == 7
    assert state.brevity == 9


def test_care_mode_reduces_pressure_and_challenge():
    state = PersonalityEngine().build(scope_type=ScopeType.PERSONAL, mode=ResponseMode.CARE)
    assert state.pressure == 1
    assert state.challenge == 4
    assert state.warmth == 10
    assert state.care == 10
    assert state.sarcasm == 2


def test_group_roast_modifier_is_clamped():
    state = PersonalityEngine().build(scope_type=ScopeType.GROUP, mode=ResponseMode.GROUP_ROAST)
    assert state.roast == 10
    assert state.sarcasm == 10
    assert state.callback == 10
    assert state.playfulness == 10


def test_participant_adaptation_applies_before_mode():
    state = PersonalityEngine().build(
        scope_type=ScopeType.PERSONAL,
        mode=ResponseMode.COACH,
        participant_adaptation={"pressure": -2, "warmth": 1},
    )
    assert state.pressure == 6
    assert state.warmth == 8


def test_serious_context_suppresses_roast_even_in_roast_mode():
    scene = SceneAnalysis(seriousness_score=0.95, conflict_score=0.2)
    state = PersonalityEngine().build(
        scope_type=ScopeType.GROUP,
        mode=ResponseMode.GROUP_ROAST,
        scene=scene,
    )
    assert state.roast <= 2
    assert state.humor <= 2
    assert state.profanity_level <= 4
    assert state.sensitivity == 10


def test_sensitive_context_increases_care_and_reduces_pressure():
    scene = SceneAnalysis(sensitivity_score=0.9)
    state = PersonalityEngine().build(
        scope_type=ScopeType.PERSONAL,
        mode=ResponseMode.ASSISTANT,
        scene=scene,
    )
    assert state.roast <= 1
    assert state.pressure <= 3
    assert state.care >= 8
    assert state.sensitivity == 10


def test_group_profanity_ceiling_cannot_be_exceeded_by_temporary_override():
    state = PersonalityEngine().build(
        scope_type=ScopeType.GROUP,
        mode=ResponseMode.GROUP_BANTER,
        context_profile={"profanity_level": 3, "profanity_frequency": 2},
        temporary_overrides={"profanity_level": 10, "profanity_frequency": 10},
    )
    assert state.profanity_level == 3
    assert state.profanity_frequency == 2


def test_profanity_level_and_frequency_are_independent_axes():
    state = PersonalityEngine().build(
        scope_type=ScopeType.GROUP,
        mode=ResponseMode.GROUP_BANTER,
        context_profile={"profanity_level": 9, "profanity_frequency": 1},
    )
    assert state.profanity_level == 9
    assert state.profanity_frequency == 1


def test_all_values_are_clamped_to_zero_ten():
    state = PersonalityEngine().build(
        scope_type=ScopeType.PERSONAL,
        mode=ResponseMode.MIRROR,
        participant_adaptation={"warmth": 100, "pressure": -100, "initiative": 100},
    )
    values = [
        state.directness, state.brevity, state.warmth, state.pressure, state.humor,
        state.sarcasm, state.roast, state.profanity_level, state.profanity_frequency,
        state.initiative, state.callback, state.challenge, state.care,
        state.playfulness, state.sensitivity,
    ]
    assert all(0 <= value <= 10 for value in values)
