from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app_v2.labs.cohost_replay import (
    CohostPlan, CohostReplayRunner, REVISION, clean_case, validate_plan,
)
from app_v2.labs.telegram_lab import LabError, atomic_json
from app_v2.labs.telegram_lab_rounds import RoundLabBot, round_report
from app_v2.services.connector_presets import available_connector_presets, build_connector_preset


def case():
    return {"id": "c1", "episode_id": "e1", "context": [
        {"id": "m1", "kind": "message", "actor": "admin", "occurred_at": "2024-03-05T09:00:00Z",
         "text": "В марте по вторникам занятие в 17:00 мск.", "reply_to": None}],
        "current": {"id": "m2", "kind": "message", "actor": "member_001",
                    "occurred_at": "2024-03-05T10:00:00Z", "text": "Сегодня в 16 или 17?", "reply_to": None}}


def plan(**changes):
    value = {"action": "answer", "topic": "schedule", "direct_to_bot": False,
             "evidence_ids": ["m1"], "reason": "grounded_help"}
    return {**value, **changes}


class Adapter:
    def __init__(self, choice=None):
        self.choice = choice if choice is not None else plan()
        self.calls = []
        self.failures = 0
    def generate_json(self, role, text, **kwargs):
        self.calls.append(("json", text, kwargs))
        return SimpleNamespace(parsed=self.choice, usage=SimpleNamespace(model="synthetic-classifier"))
    def generate_text(self, role, text, **kwargs):
        self.calls.append(("text", text, kwargs))
        return SimpleNamespace(text="По объявлению организатора занятие в 17:00 мск.",
                               usage=SimpleNamespace(model="synthetic-generator", usage_id="test-usage"))


def test_cohost_uses_past_context_before_routing_and_does_not_fake_direct_mention():
    adapter = Adapter()
    result = CohostReplayRunner(adapter).evaluate_case(case())
    classified = json.loads(adapter.calls[0][1])
    generated = json.loads(adapter.calls[1][1])
    assert classified["context"][0]["actor"] == "admin"
    assert result["decision"]["primary_action"] == "reply"
    assert result["decision"]["unsolicited"] is True
    assert result["scene"]["question_to_bot"] is False
    assert generated["action_state"]["lab_cohost_plan"]["evidence_ids"] == ["m1"]
    assert "НЕ техподдержка" not in adapter.calls[1][2]["instructions"]
    assert "Не используй характер обычного НеНоя" in adapter.calls[1][2]["instructions"]
    assert result["parity"]["scene_and_routing"].endswith("not_production_routing")


@pytest.mark.parametrize("topic", ["enrollment", "billing"])
def test_permissions_never_promoted_to_granted_by_model_plan(topic):
    checked = validate_plan(CohostPlan(**plan(topic=topic)), clean_case(case()))
    assert checked.action == "route_admin"
    assert checked.reason == "owner_required"


def test_missing_or_member_only_source_cannot_be_an_authoritative_answer():
    c = clean_case(case())
    c["context"][0]["actor"] = "member_002"
    checked = validate_plan(CohostPlan(**plan()), c)
    assert checked.action == "clarify"
    assert checked.evidence_ids == []
    checked = validate_plan(CohostPlan(**plan(evidence_ids=[])), clean_case(case()))
    assert checked.action == "clarify"


def test_unknown_evidence_is_error_not_valid_silence():
    with pytest.raises(ValueError, match="unknown evidence"):
        CohostReplayRunner(Adapter(plan(evidence_ids=["missing"]))).evaluate_case(case())


def test_future_context_is_rejected_before_model_call():
    c = case()
    c["context"][0]["occurred_at"] = "2024-03-06T09:00:00Z"
    adapter = Adapter()
    with pytest.raises(ValueError, match="future"):
        CohostReplayRunner(adapter).evaluate_case(c)
    assert adapter.calls == []


def test_rubric_is_not_sent_to_models():
    c = case()
    c.update(review_label="DO_NOT_SEND_LABEL", expected_bot_text="DO_NOT_SEND_ANSWER", curation_note="DO_NOT_SEND_NOTE")
    c["current"]["private_rubric"] = "DO_NOT_SEND_FIELD"
    adapter = Adapter()
    CohostReplayRunner(adapter).evaluate_case(c)
    assert all("DO_NOT_SEND" not in sent for _, sent, _ in adapter.calls)


def test_social_silence_does_not_call_generator():
    adapter = Adapter(plan(action="silent", topic="social", reason="social_silence", evidence_ids=[]))
    result = CohostReplayRunner(adapter).evaluate_case(case())
    assert result["generated_text"] is None
    assert result["decision"]["primary_action"] == "ignore"
    assert len(adapter.calls) == 1


def test_admin_announcements_remain_silent_even_with_high_help_plan():
    c = case()
    c["current"]["actor"] = "admin"
    adapter = Adapter()
    result = CohostReplayRunner(adapter).evaluate_case(c)
    assert result["decision"]["primary_action"] == "ignore"
    assert len(adapter.calls) == 1


def test_existing_education_preset_and_registry_are_not_mutated():
    names = available_connector_presets()
    before = build_connector_preset("education_community_v1", connector_id="test:baseline").public_state()
    CohostReplayRunner(Adapter())
    after = build_connector_preset("education_community_v1", connector_id="test:baseline").public_state()
    assert before == after
    assert names == available_connector_presets()
    assert REVISION not in names


class FakeTelegram:
    def __init__(self):
        self.messages, self.documents = [], []
    def message(self, chat, text, buttons=False):
        self.messages.append((chat, text, buttons))
    def document(self, chat, name, data):
        self.documents.append((chat, name, data))
    def call(self, method, **kwargs):
        if method == "getChatAdministrators":
            return [{"user": {"id": 910001}}]
        return True


def seed(root: Path):
    p = {"lab_replay_version": 1, "cases": [case()]}
    raw = json.dumps(p, ensure_ascii=False).encode()
    sha = hashlib.sha256(raw).hexdigest()
    p["approved_sha256"] = sha
    atomic_json(root / "pack.json", p)
    atomic_json(root / "state.json", {"offset": 100, "owner_id": 910001, "chat_id": -910001})
    atomic_json(root / "results.json", {"c1": {"status": "done", "generated_text": "BASELINE",
                                              "decision": {"primary_action": "reply", "reason_codes": []}}})
    return sha


def test_round2_snapshot_is_idempotent_and_originals_remain_byte_identical(tmp_path):
    sha = seed(tmp_path)
    old = {p.name: p.read_bytes() for p in tmp_path.glob("*.json")}
    tg = FakeTelegram()
    bot = RoundLabBot(tg, tmp_path, "synthetic-pair-code", sha)
    assert bot.active_round == "round01"
    assert bot.results["c1"]["generated_text"] == "BASELINE"
    bot.select_round2()
    assert bot.results == {}
    assert bot.state["offset"] == 100
    assert bot.state["chat_id"] == -910001
    bot.results["c1"] = {"status": "done", "generated_text": "AFTER"}
    atomic_json(bot.root / "results.json", bot.results)
    bot.select_round2()
    assert bot.results["c1"]["generated_text"] == "AFTER"
    for name, data in old.items():
        assert (tmp_path / name).read_bytes() == data
    restored = RoundLabBot(tg, tmp_path, "synthetic-pair-code", sha)
    assert restored.active_round == "round02"
    assert restored.results["c1"]["generated_text"] == "AFTER"
    assert json.loads((tmp_path / "runs/round01/results.json").read_text())["c1"]["generated_text"] == "BASELINE"


def test_cannot_switch_while_running(tmp_path):
    bot = RoundLabBot(FakeTelegram(), tmp_path, "synthetic-pair-code", seed(tmp_path))
    bot.busy = True
    with pytest.raises(LabError, match="дождись"):
        bot.select_round2()
    assert bot.active_round == "round01"


@pytest.mark.parametrize("chat,user", [(-910002, 910001), (-910001, 910002)])
def test_unpaired_chat_or_non_owner_cannot_create_round(tmp_path, chat, user):
    bot = RoundLabBot(FakeTelegram(), tmp_path, "synthetic-pair-code", seed(tmp_path))
    bot.handle({"message": {"chat": {"id": chat, "type": "group"}, "from": {"id": user}, "text": "/lab_round2"}})
    assert bot.active_round == "round01"
    assert not (tmp_path / "runs/round02").exists()
    assert bot.tg.messages == []


def test_second_round_results_report_and_no_duplicate_paid_calls(tmp_path):
    sha = seed(tmp_path)
    adapter = Adapter()
    bot = RoundLabBot(FakeTelegram(), tmp_path, "synthetic-pair-code", sha,
                      runner_factory=lambda: (CohostReplayRunner(adapter), adapter))
    bot.select_round2()
    bot.run_cases(-910001, bot.pack["cases"], False)
    assert bot.results["c1"]["status"] == "done"
    assert len(adapter.calls) == 2
    bot.start_run(-910001, False)
    assert len(adapter.calls) == 2
    assert "round02" in bot.tg.documents[0][1]
    assert "Раунд 2" in bot.tg.documents[0][2].decode()
    assert "BASELINE" in (tmp_path / "results.json").read_text()


def test_interrupted_second_round_is_not_silently_repeated(tmp_path):
    sha = seed(tmp_path)
    bot = RoundLabBot(FakeTelegram(), tmp_path, "synthetic-pair-code", sha)
    bot.select_round2()
    atomic_json(bot.root / "results.json", {"c1": {"status": "running"}})
    restored = RoundLabBot(FakeTelegram(), tmp_path, "synthetic-pair-code", sha)
    assert restored.results["c1"]["status"] == "interrupted"
    assert json.loads((tmp_path / "results.json").read_text())["c1"]["status"] == "done"


def test_report_has_revision_and_escapes_model_html(tmp_path):
    sha = seed(tmp_path)
    bot = RoundLabBot(FakeTelegram(), tmp_path, "synthetic-pair-code", sha)
    bot.select_round2()
    data = round_report(bot.root, bot.pack, {"c1": {"status": "done", "generated_text": "<script>bad()</script>"}}).decode()
    assert REVISION in data
    assert "<script>bad()" not in data
    assert "&lt;script&gt;" in data


def test_changed_revision_fingerprint_cannot_mix_into_round(tmp_path):
    sha = seed(tmp_path)
    bot = RoundLabBot(FakeTelegram(), tmp_path, "synthetic-pair-code", sha)
    bot.select_round2()
    path = bot.root / "manifest.json"
    data = json.loads(path.read_text())
    data["policy_sha256"] = "different"
    atomic_json(path, data)
    with pytest.raises(LabError, match="Смешивать"):
        bot.start_run(-910001, False)
