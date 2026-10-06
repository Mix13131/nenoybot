from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app_v2.domain.enums import ResponseMode, ScopeType
from app_v2.domain.events import SceneAnalysis
from app_v2.services.group_style import GroupStyleService, StyleEvidence
from app_v2.services.personality_engine import PersonalityEngine


NOW = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)
BASE = {
    "brevity": 5,
    "humor": 5,
    "roast": 5,
    "warmth": 5,
    "playfulness": 5,
    "callback": 5,
    "initiative": 5,
    "profanity_level": 8,
    "profanity_frequency": 5,
}


def ev(
    family: str,
    valence: str,
    actor: str,
    *,
    intervention_id: int = 1,
    days_ago: int = 0,
    feedback_type: str | None = None,
) -> StyleEvidence:
    return StyleEvidence(
        intervention_id=intervention_id,
        family=family,
        valence=valence,
        actor_key=actor,
        occurred_at=NOW - timedelta(days=days_ago, minutes=1),
        feedback_type=feedback_type or (
            "reaction_positive" if valence == "positive" else "reaction_negative"
        ),
    )


class FakeRepo:
    def __init__(self, by_scope: dict[str, list[StyleEvidence]] | None = None) -> None:
        self.by_scope = by_scope or {}
        self.calls: list[tuple[str, datetime, datetime]] = []

    def recent_evidence(self, scope_id: str, *, since: datetime, until: datetime):
        self.calls.append((scope_id, since, until))
        return list(self.by_scope.get(scope_id, ()))


def test_sparse_or_no_evidence_preserves_exact_base() -> None:
    state = GroupStyleService(FakeRepo()).evaluate("-1001", base_profile=BASE, now=NOW)
    assert state.deltas == {}
    assert state.effective_profile == BASE


def test_group_feedback_is_isolated_by_scope() -> None:
    repo = FakeRepo(
        {
            "-1001": [ev("banter_roast", "positive", "user:1"), ev("banter_roast", "positive", "user:2")],
            "-1002": [ev("banter_roast", "negative", "user:9")],
        }
    )
    service = GroupStyleService(repo)
    liked = service.evaluate("-1001", base_profile=BASE, now=NOW)
    disliked = service.evaluate("-1002", base_profile=BASE, now=NOW)

    assert liked.deltas["roast"] == 1
    assert disliked.deltas["roast"] == -2
    assert liked.effective_profile["roast"] == 6
    assert disliked.effective_profile["roast"] == 3


def test_one_prolific_positive_actor_cannot_redefine_group() -> None:
    rows = [
        ev("banter_roast", "positive", "user:1", intervention_id=index)
        for index in range(1, 12)
    ]
    state = GroupStyleService(FakeRepo({"g": rows})).evaluate("g", base_profile=BASE, now=NOW)

    assert state.deltas == {}
    assert state.evidence["banter_roast"]["positive_events"] == 11
    assert state.evidence["banter_roast"]["positive_actors"] == 1


def test_independent_positive_evidence_raises_slowly_and_is_bounded() -> None:
    two = [ev("callback", "positive", "user:1"), ev("callback", "positive", "user:2")]
    four = two + [ev("callback", "positive", "user:3"), ev("callback", "positive", "user:4")]

    state_two = GroupStyleService(FakeRepo({"g": two})).evaluate("g", base_profile=BASE, now=NOW)
    state_four = GroupStyleService(FakeRepo({"g": four})).evaluate("g", base_profile=BASE, now=NOW)

    assert state_two.deltas["callback"] == 1
    assert state_four.deltas["callback"] == 2


def test_negative_feedback_cools_faster_than_positive_learns() -> None:
    positive = [ev("banter_roast", "positive", "user:1"), ev("banter_roast", "positive", "user:2")]
    negative = [ev("banter_roast", "negative", "user:3")]

    positive_state = GroupStyleService(FakeRepo({"g": positive})).evaluate(
        "g", base_profile=BASE, now=NOW
    )
    negative_state = GroupStyleService(FakeRepo({"g": negative})).evaluate(
        "g", base_profile=BASE, now=NOW
    )

    assert positive_state.deltas["roast"] == 1
    assert negative_state.deltas["roast"] == -2


def test_old_evidence_ages_out_to_base() -> None:
    old = [
        ev("proactive", "negative", "user:1", days_ago=31),
        ev("proactive", "negative", "user:2", days_ago=31),
    ]
    state = GroupStyleService(FakeRepo({"g": old})).evaluate("g", base_profile=BASE, now=NOW)
    assert state.deltas == {}
    assert state.effective_profile == BASE


def test_generic_positive_feedback_never_increases_profanity_permission() -> None:
    rows = [
        ev("banter_roast", "positive", "user:1"),
        ev("banter_roast", "positive", "user:2"),
        ev("social_ack", "positive", "user:3"),
        ev("social_ack", "positive", "user:4"),
    ]
    state = GroupStyleService(FakeRepo({"g": rows})).evaluate("g", base_profile=BASE, now=NOW)

    assert "profanity_level" not in state.deltas
    assert "profanity_frequency" not in state.deltas
    assert state.effective_profile["profanity_level"] == BASE["profanity_level"]
    assert state.effective_profile["profanity_frequency"] == BASE["profanity_frequency"]


def test_positive_direct_reply_does_not_reinforce_complained_about_style() -> None:
    rows = [ev("direct", "positive", "user:1"), ev("direct", "positive", "user:2")]
    state = GroupStyleService(FakeRepo({"g": rows})).evaluate("g", base_profile=BASE, now=NOW)
    assert state.deltas == {}


def test_safety_scene_overrides_positive_group_style() -> None:
    rows = [ev("banter_roast", "positive", f"user:{index}") for index in range(1, 5)]
    state = GroupStyleService(FakeRepo({"g": rows})).evaluate("g", base_profile=BASE, now=NOW)

    personality = PersonalityEngine().build(
        scope_type=ScopeType.GROUP,
        mode=ResponseMode.GROUP_ROAST,
        scene=SceneAnalysis(seriousness_score=0.95, conflict_score=0.9),
        context_profile=state.effective_profile,
    )

    assert state.deltas["roast"] == 2
    assert personality.roast <= 2
    assert personality.humor <= 2


def test_metadata_is_aggregate_only() -> None:
    rows = [ev("callback", "positive", "user:secret-a"), ev("callback", "positive", "user:secret-b")]
    state = GroupStyleService(FakeRepo({"g": rows})).evaluate("g", base_profile=BASE, now=NOW)
    metadata = state.as_metadata(BASE)
    rendered = repr(metadata)

    assert "user:secret" not in rendered
    assert metadata["evidence"]["callback"]["positive_actors"] == 2
    assert metadata["version"] == "group_style_v1"


def test_personal_personality_path_is_unchanged() -> None:
    personality = PersonalityEngine().build(
        scope_type=ScopeType.PERSONAL,
        mode=ResponseMode.ASSISTANT,
        scene=SceneAnalysis(),
    )
    assert personality.roast == 3
    assert personality.initiative == 7


def test_pipeline_exposes_sanitized_style_in_action_and_intervention_metadata() -> None:
    from app_v2.domain.enums import EventType
    from tests_v2.scenarios.test_group_pipeline import event, pipeline

    style = GroupStyleService(
        FakeRepo(
            {
                "-100777": [
                    ev("banter_roast", "positive", "user:one"),
                    ev("banter_roast", "positive", "user:two"),
                ]
            }
        )
    )
    subject = pipeline(group_style=style)

    result = subject.process(
        event(EventType.DIRECT_MENTION, text="@nenoy спасибо за ответ"),
        now=NOW,
    )

    assert result.primary_action.value == "reply"
    context = subject.response_generator.calls[0]
    metadata = context.action_state["social_style"]
    assert metadata["deltas"]["roast"] == 1
    assert metadata["evidence"]["banter_roast"]["positive_actors"] == 2
    assert "user:one" not in repr(metadata)
    persisted = subject.intervention_repo.rows[0]["extra_metadata"]["social_style"]
    assert persisted == metadata
