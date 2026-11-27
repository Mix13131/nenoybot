"""Synthetic explicit-request outcomes; no provider, Telegram or database I/O."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from app_v2.coach_worker import CoachWorker, UNAVAILABLE, VALIDATION_UNAVAILABLE, _log_decision
from app_v2.domain.coach import CoachJob, SourceProof
from app_v2.repositories.coach_repo import safe_decision_reason
from app_v2.repositories.source_archive_repo import ArchivePermissionError
from app_v2.services.coach_engine import CoachDecision


class FakeRepository:
    def __init__(self, mode="direct", *, source_id="synthetic-source"):
        self.job = CoachJob(
            id="synthetic-private-job", chat_id=-987001, message_thread_id=17,
            author_user_id=987002, telegram_update_id=121, telegram_message_id=23,
            mode=mode, current_text="SYNTHETIC_PRIVATE_REQUEST", source_checksum="a" * 64,
            source_id=source_id, consent_version=4, lease_token="synthetic-private-lease",
            occurred_at=datetime.now(timezone.utc),
        )
        self.pending = self.job
        self.current = iter((True, True))
        self.outputs = []
        self.completed = []
        self.failed = []
        self.enqueue_error = None

    def assert_identity(self):
        pass

    def claim_output(self):
        return None

    def claim_job(self):
        job, self.pending = self.pending, None
        return job

    def job_is_current(self, job):
        assert job == self.job
        return next(self.current, True)

    def enqueue_output(self, job, text, **kwargs):
        if self.enqueue_error:
            raise self.enqueue_error
        self.outputs.append((job, text, kwargs))
        return "synthetic-output"

    def enqueue_outputs(self, job, texts, **kwargs):
        self.outputs.append((job, texts, kwargs))
        return ("synthetic-output",)

    def complete_job(self, job, *, reason="completed"):
        self.completed.append((job, reason))

    def fail_job(self, job, reason):
        self.failed.append((job, reason))


def worker(repo, decision, *, ai_enabled=True):
    def process(request):
        assert request.originating_job == repo.job
        return decision

    return CoachWorker(repo, engine=SimpleNamespace(process=process), sender=object(),
                       ai_enabled=ai_enabled, maintenance_enabled=False)


@pytest.mark.parametrize("mode", ["direct", "weekly_review", "track"])
@pytest.mark.parametrize("reason", [
    "fact_is_not_an_exact_source_quote", "invalid_response_shape",
    "invalid_response_value_type", "generated_silence", "response_byte_budget_exceeded",
    "unsafe_or_unsupported_output", "unsafe_experiment", "unprovided_source_ref",
])
def test_explicit_request_validation_or_model_silence_gets_source_bound_notice(mode, reason, capsys):
    repo = FakeRepository(mode)
    decision = CoachDecision("SILENCE", reason=reason)
    assert worker(repo, decision).run_once()
    assert repo.outputs == [(repo.job, VALIDATION_UNAVAILABLE, {
        "kind": "notice", "source_proofs": (SourceProof("synthetic-source", "a" * 64, 4),),
    })]
    assert repo.completed == [(repo.job, reason)] and not repo.failed
    assert json.loads(capsys.readouterr().out) == {
        "event": "coach_decision", "count": 1, "mode": mode, "action": "SILENCE", "reason": reason,
    }


@pytest.mark.parametrize("mode", ["auto_report", "index"])
@pytest.mark.parametrize("reason", ["fact_is_not_an_exact_source_quote", "generated_silence", "CoachAIError"])
def test_automatic_and_indexing_outcomes_remain_silent(mode, reason):
    repo = FakeRepository(mode)
    assert worker(repo, CoachDecision("SILENCE", reason=reason)).run_once()
    assert not repo.outputs and repo.completed == [(repo.job, reason)]


@pytest.mark.parametrize("reason", [
    "stale_job_current", "permission_denied", "stale_job_consent",
    "sensitive_or_external_current_source", "current_source_unavailable",
    "current_source_body_mismatch", "permission_or_source_changed",
    "model_flagged_sensitive_content", "no_current_text", "unknown_future_reason",
])
def test_privacy_and_unknown_refusals_do_not_create_fallback(reason):
    repo = FakeRepository()
    assert worker(repo, CoachDecision("SILENCE", reason=reason)).run_once()
    assert not repo.outputs and repo.completed == [(repo.job, reason)]


def test_fallback_rechecks_job_after_model_result():
    repo = FakeRepository()
    repo.current = iter((True, False))
    assert worker(repo, CoachDecision("SILENCE", reason="generated_silence")).run_once()
    assert not repo.outputs


def test_permission_withdrawn_during_notice_enqueue_is_silent():
    repo = FakeRepository()
    repo.enqueue_error = ArchivePermissionError("SYNTHETIC_PRIVATE_ERROR")
    assert worker(repo, CoachDecision("SILENCE", reason="generated_silence")).run_once()
    assert not repo.outputs and not repo.failed


def test_initial_stale_job_never_runs_engine_or_sends_notice():
    repo = FakeRepository()
    repo.current = iter((False,))
    instance = worker(repo, None)
    instance.engine.process = lambda request: pytest.fail("stale job reached engine")
    assert instance.run_once()
    assert not repo.outputs and not repo.completed
    assert repo.failed == [(repo.job, "feature_disabled_or_permission_changed")]


def test_direct_only_notice_does_not_require_or_claim_an_archive():
    repo = FakeRepository(source_id=None)
    assert worker(repo, CoachDecision("SILENCE", reason="generated_silence")).run_once()
    assert repo.outputs[0][1] == VALIDATION_UNAVAILABLE
    assert repo.outputs[0][2]["source_proofs"] == ()
    assert "архив" not in VALIDATION_UNAVAILABLE.casefold()


@pytest.mark.parametrize("ai_enabled,reason", [(True, "CoachAIError"), (False, "ai_disabled")])
def test_ai_unavailability_keeps_its_distinct_notice_and_reason(ai_enabled, reason):
    repo = FakeRepository()
    assert worker(repo, CoachDecision("SILENCE", reason=reason), ai_enabled=ai_enabled).run_once()
    assert repo.outputs[0][1] == UNAVAILABLE
    assert repo.completed == [(repo.job, reason)]


def test_successful_reply_is_not_replaced_by_notice_and_preserves_reason():
    repo = FakeRepository()
    decision = CoachDecision("RESPOND", reason="source_backed_decision", messages=("Synthetic reply",))
    assert worker(repo, decision).run_once()
    assert repo.outputs[0][1] == ("Synthetic reply",)
    assert repo.outputs[0][2]["kind"] == "reply"
    assert repo.completed == [(repo.job, "source_backed_decision")]


@pytest.mark.parametrize("attempt,expected", [
    (0, "coach-job:synthetic-private-job"),
    (1, "coach-job:synthetic-private-job:recovery:1"),
])
def test_operator_recovery_has_its_own_operation_namespace(attempt, expected):
    repo = FakeRepository()
    repo.job = repo.pending = replace(repo.job, recovery_attempt=attempt)
    requests = []
    instance = worker(repo, None)
    def process(request):
        requests.append(request)
        return CoachDecision("SILENCE", reason="generated_silence")
    instance.engine.process = process
    assert instance.run_once()
    assert len(requests) == 1 and requests[0].operation_id == expected
    assert requests[0].originating_job.recovery_attempt == attempt


@pytest.mark.parametrize("reason", ["private_model_text", "https://secret.invalid", {"private": "data"}, None])
def test_decision_storage_only_accepts_fixed_codes(reason):
    assert safe_decision_reason(reason) == "unclassified_decision"


def test_decision_telemetry_cannot_print_arbitrary_fields(capsys):
    _log_decision("PRIVATE_MODE", "PRIVATE_ACTION", "private_model_text")
    assert json.loads(capsys.readouterr().out) == {
        "event": "coach_decision", "count": 1, "mode": "unknown",
        "action": "unknown", "reason": "unclassified_decision",
    }
