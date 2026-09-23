from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from app_v2.labs.cohost_replay import (
    CohostPlan, CohostReplayRunner, PlanEvidenceError, clean_case, validate_plan,
)
from app_v2.labs.round_recovery import SOURCE_COMMIT, SOURCE_POLICY, safe_error
from app_v2.labs.telegram_lab import LabError, atomic_json
from app_v2.labs.telegram_lab_rounds import RoundLabBot, round_report
from tests_v2.unit.test_lab_cohost_rounds import Adapter, FakeTelegram, case, plan, seed


@pytest.mark.parametrize("actor,reason", [("admin", "admin_announcement"), ("member_001", "social_silence")])
def test_silent_plan_can_reference_current_without_treating_it_as_history(actor, reason):
    c = case()
    c["current"].update(actor=actor, text="Сегодня начинаем позже, в 18:00.")
    adapter = Adapter(plan(action="silent", evidence_ids=["m2"], reason=reason))
    result = CohostReplayRunner(adapter).evaluate_case(c)
    assert result["decision"]["primary_action"] == "ignore"
    assert result["cohost_plan"]["evidence_ids"] == []
    assert result["generated_text"] is None
    assert len(adapter.calls) == 1


def test_current_is_not_authoritative_answer_evidence():
    checked = validate_plan(CohostPlan(**plan(evidence_ids=["m2"])), clean_case(case()))
    assert checked.action == "clarify"
    assert checked.evidence_ids == []
    checked = validate_plan(CohostPlan(**plan(evidence_ids=["m2", "m1"])), clean_case(case()))
    assert checked.action == "answer"
    assert checked.evidence_ids == ["m1"]


def test_unknown_source_is_still_error_not_fake_silence():
    with pytest.raises(PlanEvidenceError):
        CohostReplayRunner(Adapter(plan(action="silent", evidence_ids=["absent"]))).evaluate_case(case())


def stopped_bot(tmp_path):
    sha = seed(tmp_path)
    pack = json.loads((tmp_path / "pack.json").read_text())
    pack["cases"] = [dict(case(), id=f"c{i}") for i in range(1, 4)]
    atomic_json(tmp_path / "pack.json", pack)
    adapter = Adapter()
    bot = RoundLabBot(FakeTelegram(), tmp_path, "synthetic-pair-code", sha,
                      runner_factory=lambda: (CohostReplayRunner(adapter), adapter))
    bot.username = "test_lab_bot"
    bot.select_round2()
    bot.results = {
        "c1": {"status": "done", "generated_text": "PRESERVE_SUCCESS", "decision": {"primary_action": "reply"}},
        "c2": {"status": "error", "error_type": "ValueError", "error": "original failure"},
    }
    atomic_json(bot.root / "results.json", bot.results)
    manifest = json.loads((bot.root / "manifest.json").read_text())
    manifest.update(git_commit=SOURCE_COMMIT, policy_sha256=SOURCE_POLICY)
    atomic_json(bot.root / "manifest.json", manifest)
    return bot, adapter


class ImmediateThread:
    def __init__(self, *, target, args, daemon):
        self.target, self.args = target, args
    def start(self):
        self.target(*self.args)


def test_explicit_resume_retries_failed_and_pending_only_preserving_evidence(tmp_path, monkeypatch):
    bot, adapter = stopped_bot(tmp_path)
    original_manifest = (bot.root / "manifest.json").read_bytes()
    baseline_files = {p: p.read_bytes() for p in (tmp_path / "runs/round01").glob("*.json")}
    original_files = {p: p.read_bytes() for p in tmp_path.glob("*.json")}
    successful = copy.deepcopy(bot.results["c1"])
    monkeypatch.setattr("app_v2.labs.telegram_lab_rounds.threading.Thread", ImmediateThread)

    # Normal run may not silently skip the failed case or bypass version checks.
    with pytest.raises(LabError, match="lab_resume"):
        bot.start_run(-910001, False)
    assert adapter.calls == []
    bot.handle({"message": {"chat": {"id": -910001, "type": "group"},
                             "from": {"id": 910001}, "text": "/lab_resume@test_lab_bot"}})
    assert len(adapter.calls) == 4  # Two calls per unfinished synthetic case.
    assert bot.results["c1"] == successful
    assert all(r["status"] == "done" for r in bot.results.values())
    assert (bot.root / "manifest.json").read_bytes() == original_manifest
    for path, data in {**baseline_files, **original_files}.items():
        assert path.read_bytes() == data
    snapshots = list((bot.root / "recovery_snapshots").glob("*/results.json"))
    assert len(snapshots) == 1
    assert json.loads(snapshots[0].read_text())["c2"]["status"] == "error"
    continuation = json.loads((bot.root / "continuation.json").read_text())
    assert continuation["retained_result_versions"]["c1"]["git_commit"] == SOURCE_COMMIT
    assert "execution_version" in bot.results["c2"]
    assert "составной отчёт" in round_report(bot.root, bot.pack, bot.results).decode()
    bot.resume_run(-910001)
    assert len(adapter.calls) == 4  # Repeated completed command cannot rerun paid calls.


@pytest.mark.parametrize("chat,user", [(-910002, 910001), (-910001, 910002)])
def test_only_paired_owner_can_authorize_recovery(tmp_path, chat, user):
    bot, adapter = stopped_bot(tmp_path)
    bot.handle({"message": {"chat": {"id": chat, "type": "group"},
                             "from": {"id": user}, "text": "/lab_resume"}})
    assert not (bot.root / "continuation.json").exists()
    assert adapter.calls == []


def test_busy_recovery_does_not_archive_or_run_again(tmp_path):
    bot, adapter = stopped_bot(tmp_path)
    bot.busy = True
    with pytest.raises(LabError, match="уже идёт"):
        bot.resume_run(-910001)
    assert not (bot.root / "recovery_snapshots").exists()
    assert adapter.calls == []


def test_unrelated_revision_change_is_not_authorized_as_incident_repair(tmp_path):
    bot, adapter = stopped_bot(tmp_path)
    path = bot.root / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["policy_sha256"] = "unrelated-change"
    atomic_json(path, manifest)
    before = (bot.root / "results.json").read_bytes()
    with pytest.raises(LabError, match="разрешённому"):
        bot.resume_run(-910001)
    assert (bot.root / "results.json").read_bytes() == before
    assert adapter.calls == []


def test_deployment_change_alone_blocks_normal_continuation(tmp_path):
    bot = RoundLabBot(FakeTelegram(), tmp_path, "synthetic-pair-code", seed(tmp_path))
    bot.select_round2()
    path = bot.root / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["git_commit"] = "different-runtime"
    atomic_json(path, manifest)
    with pytest.raises(LabError, match="Смешивать"):
        bot.start_run(-910001, False)


def test_resume_failure_is_recorded_and_does_not_become_valid_silence(tmp_path, monkeypatch):
    bot, adapter = stopped_bot(tmp_path)
    class FailingRunner:
        def evaluate_case(self, item):
            raise PlanEvidenceError("private model response MUST_NOT_LEAK")
    bot.runner_factory = lambda: (FailingRunner(), adapter)
    monkeypatch.setattr("app_v2.labs.telegram_lab_rounds.threading.Thread", ImmediateThread)
    bot.resume_run(-910001)
    assert bot.results["c2"]["status"] == "error"
    assert bot.results["c2"]["error_code"] == "unknown_evidence_id"
    assert "c3" not in bot.results
    report = round_report(bot.root, bot.pack, bot.results).decode()
    assert "MUST_NOT_LEAK" not in report
    assert "unknown_evidence_id" in report
    assert any("Прогон остановлен на технической ошибке" in text for _, text, _ in bot.tg.messages)


def test_safe_errors_never_include_exception_bodies():
    for exc in (ValueError("SECRET URL"), RuntimeError("SECRET key"), PlanEvidenceError("SECRET text")):
        code, text = safe_error(exc)
        assert "SECRET" not in code + text
