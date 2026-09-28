from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app_v2.labs import cohost_replay as previous_policy
from app_v2.labs.cohost_helpfulness import (
    CohostPlan, CohostReplayRunner, PLAN_INSTRUCTIONS, PROMPT, REVISION, clean_case, validate_plan,
)
from app_v2.labs.telegram_lab import LabError, atomic_json
from app_v2.labs.telegram_lab_rounds import RoundLabBot
from app_v2.labs.telegram_lab_focus import (
    FOCUS_CASE_IDS, FocusLabBot, focus_report, prepare_focus, select_focus_cases,
)
from app_v2.services.connector_presets import available_connector_presets, build_connector_preset
from tests_v2.unit.test_lab_cohost_rounds import Adapter, FakeTelegram, case, plan, seed
from tests_v2.unit.test_lab_round_recovery import ImmediateThread


def completed_source(root: Path):
    sha = seed(root)
    pack = json.loads((root / "pack.json").read_text())
    pack["cases"] = [dict(case(), id=cid) for cid in (*FOCUS_CASE_IDS, "synthetic_other")]
    atomic_json(root / "pack.json", pack)
    old = RoundLabBot(FakeTelegram(), root, "synthetic-pair-code", sha)
    old.select_round2()
    old.results = {c["id"]: {"status": "done", "generated_text": "KEEP_PREVIOUS",
                    "decision": {"primary_action": "reply", "reason_codes": []}} for c in pack["cases"]}
    atomic_json(old.root / "results.json", old.results)
    return sha


def new_bot(root: Path):
    sha = completed_source(root)
    adapter = Adapter()
    bot = FocusLabBot(FakeTelegram(), root, "synthetic-pair-code", sha,
                      runner_factory=lambda: (CohostReplayRunner(adapter), adapter))
    bot.username = "test_lab_bot"
    return bot, adapter, sha


def message(text, *, user=910001, chat=-910001):
    return {"message": {"chat": {"id": chat, "type": "group"}, "from": {"id": user}, "text": text}}


def test_v3_is_a_separate_policy_and_does_not_mutate_v2_or_shared_presets():
    assert previous_policy.REVISION == "education_cohost_lab_v2"
    assert REVISION == "education_cohost_lab_v3"
    assert previous_policy.PROMPT.name == "community_cohost_v2.md"
    assert PROMPT.name == "community_cohost_v3.md"
    registry = available_connector_presets()
    before = build_connector_preset("education_community_v1", connector_id="synthetic").public_state()
    CohostReplayRunner(Adapter())
    assert available_connector_presets() == registry
    assert before == build_connector_preset("education_community_v1", connector_id="synthetic").public_state()


@pytest.mark.parametrize("topic", ["access", "materials", "enrollment", "billing"])
def test_no_added_value_silent_plan_is_not_promoted_to_admin_redirect(topic):
    c = case()
    c["context"] = []
    c["current"]["text"] = "admin, уточните условия моего доступа, пожалуйста."
    adapter = Adapter(plan(action="silent", topic=topic, reason="no_added_value", evidence_ids=["m2"]))
    result = CohostReplayRunner(adapter).evaluate_case(c)
    assert result["decision"]["primary_action"] == "ignore"
    assert result["decision"]["reason_codes"] == ["cohost_no_added_value"]
    assert result["cohost_plan"]["evidence_ids"] == []
    assert len(adapter.calls) == 1


def test_v3_contract_reaches_actual_calls_without_case_ids_or_rubric():
    c = case()
    c.update(expected_bot_text="HIDDEN_RUBRIC", review_label="HIDDEN_LABEL")
    adapter = Adapter()
    result = CohostReplayRunner(adapter).evaluate_case(c)
    assert result["decision"]["primary_action"] == "reply"
    instructions = adapter.calls[0][2]["instructions"]
    assert instructions == PLAN_INSTRUCTIONS
    assert "Публичная польза" in instructions
    assert "Отсутствие подтверждения постоянного изменения НЕ доказывает разовость" in instructions
    assert "«Можно» не всегда просьба о разрешении" in instructions
    assert "no_added_value" in adapter.calls[0][2]["schema"]["properties"]["reason"]["enum"]
    generator_prompt = adapter.calls[1][2]["instructions"]
    assert "Точное время и статус решения" in generator_prompt
    assert "не требуй заново спросить администратора" in generator_prompt
    for _, payload, kwargs in adapter.calls:
        assert "HIDDEN_" not in payload
        assert all(cid not in kwargs["instructions"] for cid in FOCUS_CASE_IDS)


def test_enrollment_boundary_is_retained_for_an_explicit_bot_request():
    checked = validate_plan(CohostPlan(**plan(topic="enrollment", direct_to_bot=True)), clean_case(case()))
    assert checked.action == "route_admin"
    assert checked.evidence_ids == []


def test_suggestion_silence_and_safety_plans_keep_distinct_paths():
    silent = Adapter(plan(action="silent", topic="social", reason="social_silence", evidence_ids=[]))
    assert CohostReplayRunner(silent).evaluate_case(case())["decision"]["primary_action"] == "ignore"
    assert len(silent.calls) == 1
    safe = Adapter(plan(action="safety", topic="safety", reason="safety_boundary", evidence_ids=[]))
    assert CohostReplayRunner(safe).evaluate_case(case())["decision"]["primary_action"] == "reply"
    assert len(safe.calls) == 2


def test_focus_is_fixed_subset_not_a_changed_case_or_full_replay(tmp_path):
    sha = completed_source(tmp_path)
    source = json.loads((tmp_path / "runs/round02/pack.json").read_text())
    before = copy.deepcopy(source)
    subset = select_focus_cases(source)
    assert len(subset["cases"]) == 12
    assert [c["id"] for c in subset["cases"]] == list(FOCUS_CASE_IDS)
    assert source == before
    assert all(c in source["cases"] for c in subset["cases"])
    assert sha


def test_startup_and_restart_preserve_all_existing_artifacts_and_make_no_calls(tmp_path):
    sha = completed_source(tmp_path)
    old = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    adapter = Adapter()
    factory = lambda: (CohostReplayRunner(adapter), adapter)
    bot = FocusLabBot(FakeTelegram(), tmp_path, "synthetic-pair-code", sha, runner_factory=factory)
    assert bot.results == {}
    assert bot.state["owner_id"] == 910001
    assert bot.state["chat_id"] == -910001
    assert bot.state["offset"] == 100
    again = FocusLabBot(FakeTelegram(), tmp_path, "synthetic-pair-code", sha, runner_factory=factory)
    assert again.root == bot.root
    assert adapter.calls == []
    for p, value in old.items():
        assert p.read_bytes() == value


@pytest.mark.parametrize("text,user,chat", [
    ("/lab_check", 910002, -910001),
    ("/lab_check", 910001, -910002),
    ("/lab_check@another_bot", 910001, -910001),
    ("Обычный разговор", 910001, -910001),
])
def test_only_paired_owner_can_launch_focus(tmp_path, text, user, chat):
    bot, adapter, _ = new_bot(tmp_path)
    bot.handle(message(text, user=user, chat=chat))
    assert adapter.calls == []
    assert bot.results == {}
    assert bot.tg.messages == []


def test_one_command_runs_selected_cases_only_and_repeated_clicks_do_not_charge_again(tmp_path, monkeypatch):
    bot, adapter, _ = new_bot(tmp_path)
    previous = {p: p.read_bytes() for p in (tmp_path / "runs/round02").glob("*.json")}
    monkeypatch.setattr("app_v2.labs.telegram_lab_focus.threading.Thread", ImmediateThread)
    bot.handle(message("/lab_check@test_lab_bot"))
    assert len(bot.results) == 12
    assert all(r["status"] == "done" for r in bot.results.values())
    assert len(adapter.calls) == 24
    assert all(r["revision"] == REVISION for r in bot.results.values())
    assert bot.tg.documents[-1][1] == "group_lab_round03.html"
    report = bot.tg.documents[-1][2].decode()
    assert "12 ситуаций из 13" in report and "не повтор всех ситуаций" in report
    bot.handle(message("/lab_check"))
    bot.handle(message("/lab_resume"))
    assert len(adapter.calls) == 24
    for p, value in previous.items():
        assert p.read_bytes() == value


def test_error_and_pending_only_resume_keeps_success_and_safe_diagnostics(tmp_path, monkeypatch):
    bot, adapter, _ = new_bot(tmp_path)
    class FailSecond:
        def __init__(self):
            self.n = 0
        def evaluate_case(self, c):
            self.n += 1
            if self.n == 2:
                raise ValueError("PRIVATE_CONTENT_NOT_LOGGED")
            return {"decision": {"primary_action": "ignore", "reason_codes": []}}
    failing = FailSecond()
    bot.runner_factory = lambda: (failing, adapter)
    monkeypatch.setattr("app_v2.labs.telegram_lab_focus.threading.Thread", ImmediateThread)
    bot.handle(message("/lab_check"))
    assert len(bot.results) == 2
    first = copy.deepcopy(bot.results[FOCUS_CASE_IDS[0]])
    assert bot.results[FOCUS_CASE_IDS[1]]["status"] == "error"
    assert "PRIVATE_CONTENT" not in bot.tg.documents[-1][2].decode()
    bot.runner_factory = lambda: (CohostReplayRunner(adapter), adapter)
    bot.handle(message("/lab_resume"))
    assert len(adapter.calls) == 22
    assert bot.results[FOCUS_CASE_IDS[0]] == first
    assert len(bot.results) == 12 and all(r["status"] == "done" for r in bot.results.values())
    snapshots = list((bot.root / "recovery_snapshots").glob("*/results.json"))
    assert len(snapshots) == 1
    assert json.loads(snapshots[0].read_text())[FOCUS_CASE_IDS[1]]["status"] == "error"


@pytest.mark.parametrize("key", ["revision", "policy_sha256", "git_commit", "classifier_model", "generator_model"])
def test_other_implementation_cannot_mix_into_focus_even_with_resume(tmp_path, key):
    bot, adapter, _ = new_bot(tmp_path)
    path = bot.root / "manifest.json"
    meta = json.loads(path.read_text())
    meta[key] = "DIFFERENT"
    atomic_json(path, meta)
    with pytest.raises(LabError, match="Версия изменилась"):
        bot.start_run(-910001, retry=True)
    assert adapter.calls == []


def test_tampered_source_and_focus_packs_fail_before_calls(tmp_path):
    sha = completed_source(tmp_path)
    source = tmp_path / "runs/round02/pack.json"
    original = source.read_bytes()
    source.write_bytes(original + b" ")
    with pytest.raises(LabError, match="выборка изменилась"):
        prepare_focus(tmp_path, sha)
    assert not (tmp_path / "runs/round03").exists()
    source.write_bytes(original)
    bot = FocusLabBot(FakeTelegram(), tmp_path, "synthetic-pair-code", sha)
    (bot.root / "pack.json").write_text("{}")
    with pytest.raises(LabError, match="Целостность"):
        prepare_focus(tmp_path, sha)


def test_missing_target_case_is_not_silently_dropped():
    with pytest.raises(LabError, match="контрольных ситуаций"):
        select_focus_cases({"cases": [case()]})


def test_focus_reports_escape_html_and_keep_previous_reports_available(tmp_path):
    bot, _, _ = new_bot(tmp_path)
    cid = FOCUS_CASE_IDS[0]
    data = focus_report(bot.root, bot.pack, {cid: {"status": "done", "generated_text": "<script>bad()</script>"}}).decode()
    assert "<script>" not in data and "&lt;script&gt;" in data
    bot.handle(message("/lab_previous"))
    bot.handle(message("/lab_before"))
    assert [d[1] for d in bot.tg.documents] == ["group_lab_round02.html", "group_lab_round01.html"]
    assert "KEEP_PREVIOUS" in bot.tg.documents[0][2].decode()
