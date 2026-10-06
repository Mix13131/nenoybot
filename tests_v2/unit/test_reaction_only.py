from __future__ import annotations

from app_v2.domain.enums import EventType, PrimaryAction
from app_v2.domain.events import SceneAnalysis
from app_v2.domain.outbound import OutboundReaction
from tests_v2.scenarios.test_group_pipeline import FakeGenerator, FakeOutbox, event, pipeline


def test_safe_short_acknowledgement_reacts_without_generator_call() -> None:
    generator = FakeGenerator()
    outbox = FakeOutbox()
    subject = pipeline(generator=generator, outbox=outbox)

    result = subject.process(event(EventType.DIRECT_MENTION, text="спасибо!"))

    assert result.primary_action is PrimaryAction.REACTION_ONLY
    assert result.reaction_emoji == "👍"
    assert generator.calls == []
    outbound = outbox.calls[0]
    assert isinstance(outbound, OutboundReaction)
    assert outbound.target_message_id == "55"
    assert outbound.dedupe_key == "reaction:g1"
    assert subject.intervention_repo.rows[0]["generated_text"] is None
    assert subject.intervention_repo.rows[0]["extra_metadata"]["outbound_action"] == {
        "kind": "reaction",
        "emoji": "👍",
        "target_message_id": "55",
        "reason": "short_positive_acknowledgement",
    }


def test_duplicate_reaction_enqueue_is_idempotent() -> None:
    outbox = FakeOutbox()
    subject = pipeline(outbox=outbox)
    first = subject.process(event(EventType.DIRECT_MENTION, event_id="same", text="ахаха"))
    second = subject.process(event(EventType.DIRECT_MENTION, event_id="same", text="ахаха"))
    assert first.outbox_id == second.outbox_id
    assert first.outbox_created is True
    assert second.outbox_created is False
    assert list(outbox.by_key) == ["reaction:same"]


def test_question_and_help_request_remain_text() -> None:
    generator = FakeGenerator()
    subject = pipeline(
        generator=generator,
        scene=SceneAnalysis(question_to_bot=True, help_opportunity=0.9),
    )
    result = subject.process(event(EventType.DIRECT_MENTION, text="спасибо?"))
    assert result.primary_action is PrimaryAction.REPLY
    assert len(generator.calls) == 1


def test_serious_sensitive_or_conflict_turn_never_gets_playful_reaction() -> None:
    for field in ("seriousness_score", "sensitivity_score", "conflict_score"):
        scene = SceneAnalysis(**{field: 0.9})
        result = pipeline(scene=scene).process(
            event(EventType.DIRECT_MENTION, event_id=field, text="огонь")
        )
        assert result.primary_action is PrimaryAction.REPLY
        assert result.reaction_emoji is None


def test_scheduled_events_and_missing_target_never_become_reaction_only() -> None:
    birthday = pipeline().process(event(EventType.BIRTHDAY_DUE, text="супер"))
    assert birthday.primary_action is not PrimaryAction.REACTION_ONLY

    missing_target = event(EventType.DIRECT_MENTION, text="супер").model_copy(
        update={"message_id": None}
    )
    result = pipeline().process(missing_target)
    assert result.primary_action is PrimaryAction.REPLY
    assert result.reaction_emoji is None

