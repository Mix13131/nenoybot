from __future__ import annotations

import json
from types import SimpleNamespace

from app_v2.labs.behavior_replay import (
    BehaviorReplayOptions,
    GroupBehaviorReplayRunner,
    run_behavior_replay,
)
from app_v2.services.personality_engine import PersonalityEngine
from app_v2.services.response_generator import ResponseGenerator
from app_v2.services.scene_analyzer import SceneAnalyzer


def _scene(*, question_to_bot: bool = False, help_opportunity: float = 0.0):
    return {
        "question_to_bot": question_to_bot,
        "command_intent": None,
        "banter_score": 0.0,
        "seriousness_score": 0.1,
        "conflict_score": 0.0,
        "sensitivity_score": 0.0,
        "roast_opportunity": 0.0,
        "callback_opportunity": 0.0,
        "help_opportunity": help_opportunity,
        "memory_value": 0.0,
        "contradiction_score": 0.0,
        "commitment_signal": 0.0,
        "decision_signal": 0.0,
    }


class FakeAdapter:
    def __init__(self, scenes, text="Короткий полезный ответ."):
        self.scenes = list(scenes)
        self.text = text
        self.json_calls = []
        self.text_calls = []

    def generate_json(self, role, input_text, **kwargs):
        self.json_calls.append((role, input_text, kwargs))
        parsed = self.scenes.pop(0)
        return SimpleNamespace(
            parsed=parsed,
            text=json.dumps(parsed, ensure_ascii=False),
            usage=SimpleNamespace(model="fake-classifier", usage_id="scene-1"),
        )

    def generate_text(self, role, input_text, **kwargs):
        self.text_calls.append((role, input_text, kwargs))
        return SimpleNamespace(
            text=self.text,
            usage=SimpleNamespace(model="fake-generator", usage_id="gen-1"),
        )


def _case(case_id="c000001", text="Как правильно?", context=None):
    return {
        "id": case_id,
        "episode_id": "e00001",
        "context": list(
            context
            or [
                {
                    "id": "m000001",
                    "kind": "message",
                    "occurred_at": "2022-02-07T14:16:39",
                    "actor": "admin",
                    "text": "Сначала сделаем первый шаг.",
                    "reply_to": None,
                }
            ]
        ),
        "current": {
            "id": "m000002",
            "kind": "message",
            "occurred_at": "2022-02-07T14:17:40",
            "actor": "member_001",
            "text": text,
            "reply_to": None,
        },
        "review_label": "uncertain",
        "expected_bot_text": None,
    }


def _runner(adapter):
    return GroupBehaviorReplayRunner(
        scene_analyzer=SceneAnalyzer(adapter),
        personality_engine=PersonalityEngine(),
        response_generator=ResponseGenerator(adapter=adapter),
        options=BehaviorReplayOptions(preset_name="education_community_v1"),
    )


def test_semantic_question_to_bot_uses_current_production_direct_reply_path():
    adapter = FakeAdapter([_scene(question_to_bot=True)])
    result = _runner(adapter).evaluate_case(_case())

    assert result["decision"]["primary_action"] == "reply"
    assert result["decision"]["mode"] == "group_direct_reply"
    assert "question_to_bot" in result["decision"]["reason_codes"]
    assert result["decision"]["unsolicited"] is False
    assert result["generated_text"] == "Короткий полезный ответ."
    assert len(adapter.text_calls) == 1

    scene_input = adapter.json_calls[0][1]
    assert "CURRENT EVENT" in scene_input
    assert "RECENT CONTEXT" not in scene_input

    generator_payload = json.loads(adapter.text_calls[0][1])
    assert generator_payload["hot_messages"][0]["text"] == "Сначала сделаем первый шаг."
    assert generator_payload["event"]["text"] == "Как правильно?"


def test_low_signal_ordinary_group_turn_stays_silent_and_skips_generator():
    adapter = FakeAdapter([_scene(question_to_bot=False)])
    result = _runner(adapter).evaluate_case(_case(text="Сегодня много работы."))

    assert result["decision"]["primary_action"] == "ignore"
    assert result["generated_text"] is None
    assert adapter.text_calls == []


def test_behavior_replay_reports_partial_parity_and_has_no_live_side_effect_layers():
    adapter = FakeAdapter([_scene(question_to_bot=True)])
    runner = _runner(adapter)
    result = run_behavior_replay(
        {"lab_replay_version": 1, "cases": [_case()]},
        runner=runner,
    )

    assert result["stats"] == {
        "evaluated_cases": 1,
        "reply_cases": 1,
        "ignore_cases": 0,
    }
    parity = result["parity"]
    assert parity["scene_analyzer"] == "production_component"
    assert parity["dispatcher"] == "production_component"
    assert parity["long_memory"] == "disabled"
    assert parity["reminders_and_actions"] == "disabled"
    assert parity["telegram_outbox"] == "disabled"


def test_case_selection_and_limit_are_deterministic():
    adapter = FakeAdapter(
        [
            _scene(question_to_bot=False),
        ]
    )
    runner = _runner(adapter)
    replay = {
        "lab_replay_version": 1,
        "cases": [
            _case(case_id="c000001"),
            _case(case_id="c000002"),
        ],
    }

    result = run_behavior_replay(
        replay,
        runner=runner,
        case_ids={"c000002"},
        limit=1,
    )

    assert [item["case_id"] for item in result["results"]] == ["c000002"]
    assert result["stats"]["evaluated_cases"] == 1
