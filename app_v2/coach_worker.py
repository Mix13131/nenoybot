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
from app_v2.repositories.coach_scope_repo import CoachScopeRepository
from app_v2.repositories.source_archive_repo import ArchivePermissionError, SourceArchiveRepository
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
    def __init__(self, repo: CoachRepository, *, engine, sender, enabled=True, ai_enabled=True, dialogue=None,
                 maintenance_enabled=True):
        self.repo, self.engine, self.sender = repo, engine, sender
        self.enabled, self.ai_enabled = enabled, ai_enabled
        self.dialogue = dialogue
        self.maintenance_enabled = maintenance_enabled
        self._next_maintenance = 0.0

    def _unavailable_notice(self, job):
        if job.mode not in {"direct", "weekly_review", "track"}:
            return
        try:
            if self.repo.job_is_current(job):
                self.repo.enqueue_output(job, UNAVAILABLE, kind="notice")
        except ArchivePermissionError:
            # Removal/withdrawal can complete between the current-job check and
            # enqueue. It cancels this group, not the whole multi-group worker.
            return

    def run_once(self) -> bool:
        self.repo.assert_identity()
        if self.maintenance_enabled and time.monotonic() >= self._next_maintenance:
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
            self._unavailable_notice(job)
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
                self._unavailable_notice(job)
            self.repo.complete_job(job)
        except ArchivePermissionError:
            self.repo.fail_job(job, "group_or_permission_changed")
        except Exception as exc:
            # No hidden provider retries. Paid requests have durable reservation
            # identity, so a worker crash cannot silently pay for the same call.
            logger.warning("Coach job unavailable: %s", type(exc).__name__)
            self._unavailable_notice(job)
            self.repo.fail_job(job, "analysis_unavailable")
        return True


def build_worker(conn, config: CoachConfig, topic: int, *, chat_id: int | None = None,
                 sender=None, provider=None) -> CoachWorker:
    chat = config.chat_id if chat_id is None else chat_id
    if not config.permits_scope(chat, topic):
        raise ValueError("The coach worker scope is not permitted by configuration")
    repo = CoachRepository(conn, chat_id=chat, message_thread_id=topic,
                           bot_user_id=config.bot_user_id, transient_ttl_seconds=config.transient_ttl_seconds,
                           allow_group_messages=config.group_mode == "joined_groups")
    repo.assert_identity()
    ai = None
    if config.ai_enabled:
        budget = CoachBudgetRepository(config.database_url)
        ai = CoachAI(budget, provider or OpenAIResponsesProvider(config.openai_api_key), config.ai_config())
    engine = CoachEngine(archive=SourceArchiveRepository(
                             conn, allow_group_messages=config.group_mode == "joined_groups"), state=repo, ai=ai,
                         display_name=config.display_name)
    dialogue = CoachDialogueRepository(conn, chat_id=chat, message_thread_id=topic,
                                       bot_user_id=config.bot_user_id,
                                       transient_ttl_seconds=config.transient_ttl_seconds)
    return CoachWorker(repo, engine=engine,
                       sender=sender or CoachTelegramClient(config.telegram_bot_token),
                       enabled=config.enabled, ai_enabled=config.ai_enabled, dialogue=dialogue)


class CoachWorkerPool:
    """Discover newly joined groups/topics without restarting the worker process.

    Every turn refreshes membership before touching a cached worker. Existing
    workers are reused and removed groups are evicted. One cleanup clock serves
    the whole bot, even with no active groups. Individual jobs and deliveries
    still recheck membership under their repository locks.
    """

    def __init__(self, conn, config: CoachConfig, *, sender, provider=None):
        self.conn, self.config, self.sender = conn, config, sender
        self.registry = CoachScopeRepository(conn, bot_user_id=config.bot_user_id)
        self.registry.assert_identity()
        self.provider = provider
        if self.provider is None and config.ai_enabled:
            # One provider client and one ledger per bot, shared by every scope.
            self.provider = OpenAIResponsesProvider(config.openai_api_key)
        self.workers: dict[tuple[int, int], CoachWorker] = {}
        self._next_maintenance = 0.0

    def run_once(self) -> bool:
        self.registry.assert_identity()
        if time.monotonic() >= self._next_maintenance:
            self.registry.maintenance(transient_ttl_seconds=self.config.transient_ttl_seconds)
            self._next_maintenance = time.monotonic() + 60
        admitted = {
            scope for scope in self.registry.list_scopes(include_disabled=True)
            if self.config.permits_scope(*scope)
        }
        for scope in self.workers.keys() - admitted:
            del self.workers[scope]
        for chat, topic in sorted(admitted - self.workers.keys()):
            worker = build_worker(
                self.conn, self.config, topic, chat_id=chat,
                sender=self.sender, provider=self.provider,
            )
            worker.maintenance_enabled = False
            self.workers[(chat, topic)] = worker
        worked = False
        for worker in self.workers.values():
            worked = worker.run_once() or worked
        return worked


def main() -> int:
    config = load_coach_config()
    if not all((config.database_url, config.telegram_bot_token, config.scope_configured)):
        raise RuntimeError("Configure the isolated coach before starting its worker")
    sender = CoachTelegramClient(config.telegram_bot_token)
    try:
        with connect(config.database_url, autocommit=True, connect_timeout=5) as conn:
            pool = CoachWorkerPool(conn, config, sender=sender)
            while True:
                if not pool.run_once():
                    time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    finally:
        sender.close()


if __name__ == "__main__":
    raise SystemExit(main())
