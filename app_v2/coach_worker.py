"""Separate coach worker; never consumes the ordinary NeNoi queues."""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import time

from app_v2.adapters.coach_telegram import CoachTelegramClient
from app_v2.adapters.postgres import connect
from app_v2.coach_config import CoachConfig, load_coach_config
from app_v2.repositories.coach_budget_repo import CoachBudgetRepository
from app_v2.repositories.coach_dialogue_repo import CoachDialogueRepository
from app_v2.repositories.coach_repo import CoachRepository
from app_v2.repositories.source_archive_repo import SourceArchiveRepository
from app_v2.services.coach_ai import CoachAI, OpenAIResponsesProvider
from app_v2.services.coach_engine import CoachEngine, CoachRequest


logger = logging.getLogger(__name__)
UNAVAILABLE = (
    "Сейчас разбор недоступен: AI выключен, недоступен или остановлен защитой расходов. "
    "Я не делал выводов по твоему сообщению. Разрешённый архив сохраняется независимо от анализа. "
    "Можно вернуться к этому чуть позже."
)
_AI_UNAVAILABLE_REASONS = {
    "CoachAIError", "CoachAIConfigurationError", "CoachBudgetConfigurationError",
    "CoachBudgetExceeded", "CoachOperationAlreadyAttempted",
}


class CoachWorker:
    def __init__(self, repo: CoachRepository, *, engine, sender, enabled=True, ai_enabled=True, dialogue=None):
        self.repo, self.engine, self.sender = repo, engine, sender
        self.enabled, self.ai_enabled = enabled, ai_enabled
        self.dialogue = dialogue
        self._next_maintenance = 0.0

    def run_once(self) -> bool:
        self.repo.assert_identity()
        if time.monotonic() >= self._next_maintenance:
            self.repo.maintenance()
            self._next_maintenance = time.monotonic() + 60
        output = self.repo.claim_output()
        if output is not None:
            if output.mode != "control" and not self.enabled:
                self.repo.cancel_output(output, reason="feature_disabled")
            else:
                self.repo.deliver_output(output, self.sender.send,
                                         on_sent=self.dialogue.record_sent if self.dialogue else None)
            return True
        job = self.repo.claim_job()
        if job is None:
            return False
        if not self.enabled or not self.repo.job_is_current(job):
            self.repo.fail_job(job, "feature_disabled_or_permission_changed")
            return True
        if not self.ai_enabled:
            if job.mode in {"direct", "weekly_review", "track"}:
                self.repo.enqueue_output(job, UNAVAILABLE, kind="notice")
            self.repo.complete_job(job)
            return True
        request = CoachRequest(
            operation_id="coach-job:" + job.id,
            mode=job.mode, chat_id=job.chat_id, message_thread_id=job.message_thread_id,
            author_user_id=job.author_user_id, current_text=job.current_text,
            source_id=job.source_id, source_checksum=job.source_checksum,
            consent_version=job.consent_version, now=datetime.now(timezone.utc),
            days=job.window_days, public_confirmed=job.public_confirmed,
            current_guard=lambda: self.repo.job_is_current(job),
            originating_job=job,
        )
        try:
            decision = self.engine.process(request)
            if decision.messages and decision.action != "SILENCE":
                kind = "notice" if decision.reason == "no_authorized_evidence" else "reply"
                self.repo.enqueue_outputs(job, decision.messages, source_proofs=decision.dependencies,
                                          kind=kind, experiment_links=decision.experiment_links)
            elif (decision.reason in _AI_UNAVAILABLE_REASONS and
                  job.mode in {"direct", "weekly_review", "track"}):
                self.repo.enqueue_output(job, UNAVAILABLE, kind="notice")
            self.repo.complete_job(job)
        except Exception as exc:
            # No hidden provider retries. Paid requests have durable reservation
            # identity, so a worker crash cannot silently pay for the same call.
            logger.warning("Coach job unavailable: %s", type(exc).__name__)
            if job.mode in {"direct", "weekly_review", "track"}:
                self.repo.enqueue_output(job, UNAVAILABLE, kind="notice")
            self.repo.fail_job(job, "analysis_unavailable")
        return True


def build_worker(conn, config: CoachConfig, topic: int, *, sender=None, provider=None) -> CoachWorker:
    repo = CoachRepository(conn, chat_id=config.chat_id, message_thread_id=topic,
                           bot_user_id=config.bot_user_id, transient_ttl_seconds=config.transient_ttl_seconds)
    repo.assert_identity()
    ai = None
    if config.ai_enabled:
        budget = CoachBudgetRepository(config.database_url)
        ai = CoachAI(budget, provider or OpenAIResponsesProvider(config.openai_api_key), config.ai_config())
    engine = CoachEngine(archive=SourceArchiveRepository(conn), state=repo, ai=ai)
    dialogue = CoachDialogueRepository(conn, chat_id=config.chat_id, message_thread_id=topic,
                                       bot_user_id=config.bot_user_id,
                                       transient_ttl_seconds=config.transient_ttl_seconds)
    return CoachWorker(repo, engine=engine,
                       sender=sender or CoachTelegramClient(config.telegram_bot_token),
                       enabled=config.enabled, ai_enabled=config.ai_enabled, dialogue=dialogue)


def main() -> int:
    config = load_coach_config()
    if not all((config.database_url, config.telegram_bot_token, config.chat_id, config.topic_ids)):
        raise RuntimeError("Configure the isolated coach before starting its worker")
    sender = CoachTelegramClient(config.telegram_bot_token)
    try:
        with connect(config.database_url, autocommit=True, connect_timeout=5) as conn:
            workers = [build_worker(conn, config, topic, sender=sender) for topic in config.topic_ids]
            while True:
                worked = False
                for worker in workers:
                    worked = worker.run_once() or worked
                if not worked:
                    time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    finally:
        sender.close()


if __name__ == "__main__":
    raise SystemExit(main())
