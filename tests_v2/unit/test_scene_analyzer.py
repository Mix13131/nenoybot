from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app_v2.domain.enums import EventType, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.services.scene_analyzer import SceneAnalyzer


def _event(event_type: EventType = EventType.GROUP_MESSAGE) -> EventEnvelope:
    return EventEnvelope(
        event_id="evt-scene",
        event_type=event_type,
        occurred_at=datetime.now(timezone.utc),
        scope_type=ScopeType.GROUP,
        scope_id="g1",
        actor_user_id="u1",
        message_id="m1",
        text="Ну да, конечно",
        metadata={"reply_to_bot": event_type is EventType.REPLY_TO_BOT},
    )


def _payload(**overrides):
    base = {
        "question_to_bot": False,
        "command_intent": None,
        "banter_score": 0.2,
        "seriousness_score": 0.2,
        "conflict_score": 0.1,
        "sensitivity_score": 0.1,
        "roast_opportunity": 0.2,
        "callback_opportunity": 0.2,
        "help_opportunity": 0.1,
        "memory_value": 0.1,
        "contradiction_score": 0.1,
        "commitment_signal": 0.0,
        "decision_signal": 0.0,
    }
    base.update(overrides)
    return base


class FakeAdapter:
    def __init__(self, payload=None, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.calls = []

    def generate_json(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        return SimpleNamespace(parsed=self.payload)


def test_ordinary_banter_maps_structured_result() -> None:
    adapter = FakeAdapter(_payload(banter_score=0.85, roast_opportunity=0.55))
    analyzer = SceneAnalyzer(adapter, instructions="analyze")

    scene = analyzer.analyze(_event(), recent_context="Серёга шутит")

    assert scene.banter_score == 0.85
    assert scene.roast_opportunity == 0.55
    assert scene.direct_mention is False
    assert adapter.calls[0][1]["schema_name"] == "scene_analysis"
    assert "RECENT CONTEXT" in adapter.calls[0][0][1]


def test_real_conflict_maps_high_seriousness_and_low_roast() -> None:
    analyzer = SceneAnalyzer(
        FakeAdapter(
            _payload(
                seriousness_score=0.95,
                conflict_score=0.92,
                sensitivity_score=0.8,
                roast_opportunity=0.05,
            )
        ),
        instructions="analyze",
    )
    scene = analyzer.analyze(_event())

    assert scene.seriousness_score == 0.95
    assert scene.conflict_score == 0.92
    assert scene.roast_opportunity == 0.05


def test_contradiction_fixture_maps_signal() -> None:
    analyzer = SceneAnalyzer(
        FakeAdapter(_payload(contradiction_score=0.94, callback_opportunity=0.86)),
        instructions="analyze",
    )
    scene = analyzer.analyze(_event(), recent_context="Вчера говорил обратное")

    assert scene.contradiction_score == 0.94
    assert scene.callback_opportunity == 0.86


def test_direct_mention_is_preserved_deterministically() -> None:
    analyzer = SceneAnalyzer(FakeAdapter(_payload()), instructions="analyze")
    scene = analyzer.analyze(_event(EventType.DIRECT_MENTION))

    assert scene.direct_mention is True


def test_reply_to_bot_is_preserved_deterministically() -> None:
    analyzer = SceneAnalyzer(FakeAdapter(_payload()), instructions="analyze")
    scene = analyzer.analyze(_event(EventType.REPLY_TO_BOT))

    assert scene.reply_to_bot is True


def test_invalid_model_payload_returns_conservative_fallback() -> None:
    analyzer = SceneAnalyzer(
        FakeAdapter(_payload(roast_opportunity=1.5)),
        instructions="analyze",
    )
    scene = analyzer.analyze(_event())

    assert scene.roast_opportunity == 0.0
    assert scene.callback_opportunity == 0.0
    assert scene.seriousness_score == 0.75
    assert scene.sensitivity_score == 0.75


def test_adapter_failure_returns_conservative_fallback_and_keeps_direct_signal() -> None:
    analyzer = SceneAnalyzer(FakeAdapter(error=TimeoutError("timeout")), instructions="analyze")
    scene = analyzer.analyze(_event(EventType.DIRECT_MENTION))

    assert scene.direct_mention is True
    assert scene.roast_opportunity == 0.0
    assert scene.sensitivity_score == 0.75
