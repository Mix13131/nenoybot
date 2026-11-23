"""Durable, scoped state for one isolated coach bot; no paid model dependencies.

The outer webhook transaction owns intake. Worker claims commit in their own
transactions when the connection is idle/autocommit. A sending lease is never
retried: a crash or timeout can leave Telegram's outcome unknown.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import re
from typing import Callable, Sequence
from uuid import uuid4

from app_v2.adapters.telegram_archive import archive_message_from_update
from app_v2.repositories.coach_scope_repo import CoachConfigurationError, CoachScopeRepository, cancel_failed_bundle_tails
from app_v2.domain.coach import CoachJob, CoachOutput, CoachPermissions, GRANT_FIELDS, SourceProof
from app_v2.domain.source_archive import ArchiveMessage
from app_v2.repositories.source_archive_repo import ArchivePermissionError, SourceArchiveRepository


logger=logging.getLogger(__name__)


_DECISION_REASONS = frozenset({
    "completed", "ai_disabled", "source_backed_decision", "no_authorized_evidence",
    "indexed_without_publication", "generated_silence", "stale_job_current",
    "permission_denied", "stale_job_consent", "sensitive_or_external_current_source",
    "current_source_unavailable", "current_source_body_mismatch",
    "automatic_processing_requires_durable_source", "no_current_text",
    "permission_or_source_changed", "model_flagged_sensitive_content",
    "unsafe_or_unsupported_output", "unsafe_experiment", "invalid_output_text",
    "invalid_response_value_type", "source_refs_required", "invalid_source_ref",
    "unprovided_source_ref", "invalid_response_shape", "response_byte_budget_exceeded",
    "invalid_facts", "invalid_claim_flag", "invalid_risk_flags", "invalid_fact",
    "fact_is_not_an_exact_source_quote", "invalid_hypotheses", "invalid_hypothesis",
    "first_time_requires_explicit_self_report", "recurrence_requires_independent_episodes",
    "invalid_search_terms", "invalid_dialogue_action", "invalid_experiments", "invalid_gap",
    "silence_has_no_publication", "response_text_required", "invalid_dialogue_message",
    "invalid_experiment", "unbounded_experiment", "experiment_message_required",
    "experiment_must_be_in_the_linked_message", "known_experiment_must_not_be_reoffered",
    "CoachAIError", "CoachAIConfigurationError", "CoachBudgetConfigurationError",
    "CoachBudgetExceeded", "CoachOperationAlreadyAttempted",
})


def safe_decision_reason(reason: object) -> str:
    """Persist/log only fixed technical outcomes, never model or exception text."""
    return reason if isinstance(reason, str) and reason in _DECISION_REASONS else "unclassified_decision"


def _now(value=None):
    value = value or datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("A timezone-aware clock is required")
    return value.astimezone(timezone.utc)


def _proof_json(proofs):
    return json.dumps([asdict(item) for item in proofs], sort_keys=True, separators=(",", ":"))


class CoachRepository:
    def __init__(self, conn, *, chat_id: int, message_thread_id: int, bot_user_id: int,
                 transient_ttl_seconds: int = 86400, allow_group_messages: bool = False):
        if type(chat_id) is not int or chat_id >= 0:
            raise ValueError("A negative Telegram group ID is required")
        if any(type(v) is not int or v <= 0 for v in (message_thread_id, bot_user_id)):
            raise ValueError("Explicit topic and bot IDs are required")
        if type(transient_ttl_seconds) is not int or not 60 <= transient_ttl_seconds <= 86400:
            raise ValueError("Transient text lifetime must be between one minute and 24 hours")
        self.conn, self.chat_id, self.message_thread_id = conn, chat_id, message_thread_id
        self.bot_user_id, self.transient_ttl_seconds = bot_user_id, transient_ttl_seconds
        self.allow_group_messages = allow_group_messages
        self.archive = SourceArchiveRepository(conn, allow_group_messages=allow_group_messages)

    def _key(self, author):
        if type(author) is not int or author <= 0:
            raise ValueError("A trusted individual author ID is required")
        return self.chat_id, self.message_thread_id, author

    def bind_identity(self):
        CoachScopeRepository(self.conn, bot_user_id=self.bot_user_id).bind_identity()

    def assert_identity(self):
        CoachScopeRepository(self.conn, bot_user_id=self.bot_user_id).assert_identity()

    def scope_enabled(self) -> bool:
        row=self.conn.execute("SELECT s.enabled AND COALESCE(g.active,TRUE) FROM source_archive_scopes s "
                              "LEFT JOIN coach_group_memberships g USING(chat_id) WHERE s.chat_id=%s AND s.message_thread_id=%s",
                              (self.chat_id,self.message_thread_id)).fetchone()
        return bool(row and row[0])

    def authenticated_message(self, normalized):
        message = archive_message_from_update(normalized, allow_group_messages=self.allow_group_messages)
        if message is None or (message.chat_id, message.message_thread_id) != (self.chat_id, self.message_thread_id):
            return None
        return message

    @contextmanager
    def author_lock(self, author_user_id: int, *, require_enabled: bool = True):
        """Group membership, then scope, then individual consent through send."""
        key = self._key(author_user_id)
        with self.conn.transaction():
            # Old explicitly configured scopes may predate membership tracking.
            # Insert-only compatibility registration gives removal the same lock
            # boundary; an existing tombstone is never changed here.
            self.conn.execute(
                "INSERT INTO coach_group_memberships(chat_id,active,active_since,changed_at) "
                "VALUES(%s,TRUE,'1970-01-01 UTC',CURRENT_TIMESTAMP) ON CONFLICT DO NOTHING",
                (self.chat_id,),
            )
            membership = self.conn.execute(
                "SELECT active FROM coach_group_memberships WHERE chat_id=%s FOR SHARE", (self.chat_id,),
            ).fetchone()
            if membership is not None and not membership[0]:
                raise ArchivePermissionError("The bot is no longer in this group")
            scope = self.conn.execute(
                "SELECT enabled FROM source_archive_scopes WHERE chat_id=%s AND message_thread_id=%s FOR SHARE",
                key[:2],
            ).fetchone()
            if not scope or (require_enabled and not scope[0]):
                raise ArchivePermissionError("The exact coach topic is not enabled")
            self.conn.execute(
                "SELECT version FROM source_archive_consents WHERE chat_id=%s AND message_thread_id=%s "
                "AND author_user_id=%s FOR UPDATE", key,
            ).fetchone()
            yield

    def ensure_participant(self, message: ArchiveMessage):
        """Called only for an authenticated person's explicit control request."""
        if (message.chat_id, message.message_thread_id) != (self.chat_id, self.message_thread_id):
            raise ArchivePermissionError("Wrong coach topic")
        author = message.author_user_id
        with self.author_lock(author, require_enabled=False):
            if not self.permissions(author).version:
                self.archive.set_permissions(chat_id=self.chat_id, message_thread_id=self.message_thread_id,
                                             author_user_id=author, actor_user_id=author)
            self.conn.execute(
                "INSERT INTO coach_participant_state(chat_id,message_thread_id,author_user_id) VALUES(%s,%s,%s) "
                "ON CONFLICT DO NOTHING", self._key(author),
            )

    def bootstrap_group_default(self, normalized, *, now=None) -> bool:
        """Initialize a first human under this configured group's default policy."""
        message=self.authenticated_message(normalized)
        if message is None:
            return False
        self.assert_identity()
        with self.author_lock(message.author_user_id,require_enabled=False):
            if not self.scope_enabled():
                return False
            created=self.archive.bootstrap_group_default(normalized,now=now)
            if created:
                self.conn.execute(
                    "INSERT INTO coach_participant_state(chat_id,message_thread_id,author_user_id,track_status) "
                    "VALUES(%s,%s,%s,'active') ON CONFLICT DO NOTHING",self._key(message.author_user_id),
                )
            return created

    def permissions(self, author_user_id: int) -> CoachPermissions:
        row = self.conn.execute(
            """SELECT c.version,c.retain_source,c.historical_inference,c.auto_report_analysis,
                      c.unsolicited_group_reply,c.direct_request_group_reply,
                      COALESCE(s.track_status,'off'),COALESCE(s.disclosure_version=1 AND
                          s.disclosure_delivered_at IS NOT NULL,FALSE),c.grant_basis
               FROM source_archive_consents c LEFT JOIN coach_participant_state s
                 USING(chat_id,message_thread_id,author_user_id)
               WHERE c.chat_id=%s AND c.message_thread_id=%s AND c.author_user_id=%s""",
            self._key(author_user_id),
        ).fetchone()
        return CoachPermissions(*row) if row else CoachPermissions()

    def conversation_context(self, author_user_id: int, *, max_bytes=12000, before=None):
        from app_v2.repositories.coach_dialogue_repo import CoachDialogueRepository
        return CoachDialogueRepository(self.conn,chat_id=self.chat_id,message_thread_id=self.message_thread_id,
            bot_user_id=self.bot_user_id,transient_ttl_seconds=self.transient_ttl_seconds).context(
                author_user_id,max_bytes=max_bytes,before=before)

    def claim_ingest(self, telegram_update_id: int) -> bool:
        if type(telegram_update_id) is not int or telegram_update_id < 0:
            raise ValueError("A Telegram update ID is required")
        return self.conn.execute(
            "INSERT INTO coach_ingest_receipts(telegram_update_id,chat_id,message_thread_id) VALUES(%s,%s,%s) "
            "ON CONFLICT DO NOTHING RETURNING telegram_update_id",
            (telegram_update_id, self.chat_id, self.message_thread_id),
        ).fetchone() is not None

    def claim_control(self, message: ArchiveMessage) -> bool:
        self.ensure_participant(message)
        key = self._key(message.author_user_id)
        row = self.conn.execute(
            "SELECT last_control_occurred_at,last_control_update_id FROM coach_participant_state "
            "WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s FOR UPDATE", key,
        ).fetchone()
        if row[0] is not None and (message.occurred_at, message.telegram_update_id) <= tuple(row):
            return False
        self.conn.execute(
            "UPDATE coach_participant_state SET last_control_occurred_at=%s,last_control_update_id=%s "
            "WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s",
            (message.occurred_at, message.telegram_update_id, *key),
        )
        return True

    def change_permissions(self, message: ArchiveMessage, **changes) -> int:
        if set(changes) - set(GRANT_FIELDS):
            raise ValueError("Unknown permission")
        with self.author_lock(message.author_user_id, require_enabled=any(changes.values())):
            existing = self.permissions(message.author_user_id)
            flags = {key: getattr(existing, key) for key in GRANT_FIELDS}
            flags.update(changes)
            return self.archive.set_permissions(
                chat_id=self.chat_id,message_thread_id=self.message_thread_id,
                author_user_id=message.author_user_id,actor_user_id=message.author_user_id,**flags,
            )

    def set_track_status(self, author_user_id: int, status: str):
        if status not in {"off", "active", "paused"}:
            raise ValueError("Invalid track status")
        with self.author_lock(author_user_id, require_enabled=status == "active"):
            permissions = self.permissions(author_user_id)
            if status == "active" and not (permissions.retain_source and permissions.historical_inference):
                raise ArchivePermissionError("A personal track requires retention and historical inference")
            self.conn.execute(
                "UPDATE coach_participant_state SET track_status=%s "
                "WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s",
                (status, *self._key(author_user_id)),
            )
            if status != "active":
                self.conn.execute(
                    "UPDATE coach_jobs SET status='cancelled',current_text=NULL,source_checksum=NULL,source_id=NULL "
                    "WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s "
                    "AND mode='track' AND status IN ('pending','processing')", self._key(author_user_id),
                )
                self.conn.execute(
                    "UPDATE coaching_outputs SET status='cancelled',text=NULL,source_proofs='[]'::jsonb,request_checksum=NULL "
                    "WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s "
                    "AND mode='track' AND status IN ('pending','sending')", self._key(author_user_id),
                )

    def forget(self, message: ArchiveMessage):
        with self.author_lock(message.author_user_id, require_enabled=False):
            self.conn.execute("UPDATE coach_participant_state SET memory_epoch=memory_epoch+1 "
                "WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s",self._key(message.author_user_id))
            retained=self.permissions(message.author_user_id).retain_source
            receipt = self.archive.forget_sources(
                chat_id=self.chat_id,message_thread_id=self.message_thread_id,
                author_user_id=message.author_user_id,actor_user_id=message.author_user_id,
            )
            if not retained:
                # Stage A advances its retention cutoff only when retention is
                # enabled. Direct-only forgetting also needs a new request epoch,
                # without changing any of the five independent permission flags.
                self.change_permissions(message)
            self._cancel_author(message.author_user_id)
            self.conn.execute(
                "UPDATE coach_episodes SET summary=NULL,source_proofs='[]'::jsonb,is_active=FALSE "
                "WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s", self._key(message.author_user_id),
            )
            self.set_track_status(message.author_user_id, "off")
            return receipt

    def delete_track(self, message: ArchiveMessage) -> int:
        """Delete this author's derived track, retaining independently allowed sources."""
        if (message.chat_id,message.message_thread_id)!=(self.chat_id,self.message_thread_id):
            raise ArchivePermissionError("Wrong coach topic")
        with self.author_lock(message.author_user_id,require_enabled=False):
            key=self._key(message.author_user_id)
            self.conn.execute("UPDATE coach_participant_state SET memory_epoch=memory_epoch+1 "
                "WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s",key)
            count=self.conn.execute(
                "UPDATE coach_episodes SET summary=NULL,source_proofs='[]'::jsonb,is_active=FALSE "
                "WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s AND is_active",key,
            ).rowcount
            self.set_track_status(message.author_user_id,"off")
            self.conn.execute(
                "UPDATE coach_jobs SET status='cancelled',current_text=NULL,source_checksum=NULL,source_id=NULL,"
                "lease_token=NULL,lease_until=NULL,reason='track_deleted' "
                "WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s "
                "AND status IN ('pending','processing')",key,
            )
            self.conn.execute(
                "UPDATE coaching_outputs SET status='cancelled',text=NULL,source_proofs='[]'::jsonb,"
                "request_checksum=NULL,source_id=NULL,lease_token=NULL,lease_until=NULL "
                "WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s "
                "AND mode<>'control' AND status IN ('pending','sending')",key,
            )
            return count

    def _cancel_author(self, author):
        key = self._key(author)
        self.conn.execute(
            "UPDATE coach_jobs SET status='cancelled',current_text=NULL,source_checksum=NULL,source_id=NULL,lease_token=NULL "
            "WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s AND status IN ('pending','processing')", key,
        )
        self.conn.execute(
            "UPDATE coaching_outputs SET status='cancelled',text=NULL,source_proofs='[]'::jsonb,request_checksum=NULL,source_id=NULL,lease_token=NULL "
            "WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s AND status IN ('pending','sending')", key,
        )
        self.conn.execute(
            "UPDATE coach_message_versions SET checksum=NULL WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s", key,
        )

    @staticmethod
    def _mode_allowed(permission: CoachPermissions, mode: str) -> bool:
        if mode == "direct":
            return permission.direct_request_group_reply
        if mode == "auto_report":
            return permission.automatic_allowed
        if mode == "index":
            return permission.retain_source and permission.historical_inference
        if mode in {"weekly_review", "track"}:
            return (permission.direct_request_group_reply and permission.retain_source
                    and permission.historical_inference and (mode != "track" or permission.track_status == "active"))
        return False

    def invalidate_message(self, message: ArchiveMessage, *, now=None) -> bool:
        if (message.chat_id,message.message_thread_id) != (self.chat_id,self.message_thread_id):
            return False
        with self.author_lock(message.author_user_id,require_enabled=False):
            key = (*self._key(message.author_user_id),message.telegram_message_id)
            current = self.conn.execute(
                "SELECT revision_at,telegram_update_id,checksum FROM coach_message_versions "
                "WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s AND telegram_message_id=%s FOR UPDATE", key,
            ).fetchone()
            if not current:
                permission=self.permissions(message.author_user_id)
                if not message.is_edited or not (permission.direct_request_group_reply or
                        (permission.retain_source and permission.historical_inference)):
                    return False
                # An edit can arrive before its original. Keep only its ordering
                # watermark, never text or a checksum, to reject that later old
                # original. Maintenance clears hashes but preserves watermarks.
                self.conn.execute(
                    "INSERT INTO coach_message_versions(chat_id,message_thread_id,author_user_id,telegram_message_id,"
                    "telegram_update_id,revision_at,checksum,expires_at) VALUES(%s,%s,%s,%s,%s,%s,NULL,%s)",
                    (*key,message.telegram_update_id,message.revision_at,
                     _now(now)+timedelta(seconds=self.transient_ttl_seconds)),
                )
                return True
            if (message.revision_at,message.telegram_update_id) <= tuple(current[:2]):
                return False
            self.conn.execute(
                "UPDATE coach_message_versions SET revision_at=%s,telegram_update_id=%s,checksum=NULL,expires_at=%s "
                "WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s AND telegram_message_id=%s",
                (message.revision_at,message.telegram_update_id,
                 _now(now)+timedelta(seconds=self.transient_ttl_seconds),*key),
            )
            self.conn.execute(
                "UPDATE coach_jobs SET status='cancelled',current_text=NULL,source_checksum=NULL,source_id=NULL,lease_token=NULL "
                "WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s AND telegram_message_id=%s "
                "AND status IN ('pending','processing')", key,
            )
            self.conn.execute(
                "UPDATE coaching_outputs SET status='cancelled',text=NULL,source_proofs='[]'::jsonb,request_checksum=NULL,source_id=NULL,lease_token=NULL "
                "WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s AND request_message_id=%s "
                "AND mode<>'control' AND status IN ('pending','sending')", key,
            )
            return True

    def enqueue_job(self, normalized, mode: str, *, source_id: str | None = None,
                    window_days: int = 7, public_confirmed: bool = False, now=None) -> str | None:
        message = self.authenticated_message(normalized)
        if message is None or message.available_text is None or message.contains_external_content:
            return None
        if mode not in {"direct","auto_report","weekly_review","track","index"}:
            raise ValueError("Unknown coach job mode")
        if type(window_days) is not int or not 1 <= window_days <= 3660 or type(public_confirmed) is not bool:
            raise ValueError("Invalid request options")
        if source_id is not None and source_id != message.source_id:
            raise ValueError("Current source provenance mismatch")
        now = _now(now)
        with self.author_lock(message.author_user_id,require_enabled=False):
            if not self.scope_enabled():
                return None
            permissions = self.permissions(message.author_user_id)
            if not self._mode_allowed(permissions,mode):
                return None
            # Only short-lived AI work has a bounded arrival age. Independent
            # source capture happens before this method and is not restricted.
            if message.occurred_at <= now-timedelta(seconds=self.transient_ttl_seconds):
                return None
            grant=self.conn.execute(
                "SELECT updated_at,retain_since,grant_basis FROM source_archive_consents WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s",
                self._key(message.author_user_id),
            ).fetchone()
            granted_at=grant[1] if grant[2]=="group_default" else grant[0]
            boundary=granted_at.replace(microsecond=0)+(timedelta(seconds=1) if granted_at.microsecond else timedelta())
            if message.occurred_at<boundary:
                return None  # A later opt-in never authorizes a delayed earlier request.
            if self.conn.execute(
                "SELECT 1 FROM source_archive_deletions WHERE chat_id=%s AND message_thread_id=%s "
                "AND author_user_id=%s AND telegram_message_id=%s",
                (*self._key(message.author_user_id),message.telegram_message_id),
            ).fetchone():
                return None
            expiry = now + timedelta(seconds=self.transient_ttl_seconds)
            key = (*self._key(message.author_user_id),message.telegram_message_id)
            self.invalidate_message(message, now=now)
            self.conn.execute(
                "INSERT INTO coach_message_versions(chat_id,message_thread_id,author_user_id,telegram_message_id,telegram_update_id,revision_at,checksum,expires_at) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(chat_id,message_thread_id,author_user_id,telegram_message_id) "
                "DO UPDATE SET checksum=EXCLUDED.checksum,expires_at=EXCLUDED.expires_at "
                "WHERE coach_message_versions.telegram_update_id=EXCLUDED.telegram_update_id "
                "AND coach_message_versions.revision_at=EXCLUDED.revision_at",
                (*key,message.telegram_update_id,message.revision_at,message.checksum,expiry),
            )
            current = self.conn.execute(
                "SELECT checksum FROM coach_message_versions WHERE chat_id=%s AND message_thread_id=%s "
                "AND author_user_id=%s AND telegram_message_id=%s",key,
            ).fetchone()
            if current[0] != message.checksum:
                return None
            identifier = uuid4().hex
            row = self.conn.execute(
                """INSERT INTO coach_jobs(id,telegram_update_id,chat_id,message_thread_id,author_user_id,
                   telegram_message_id,mode,current_text,source_checksum,source_id,consent_version,occurred_at,
                   window_days,public_confirmed,created_at,expires_at)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(telegram_update_id) DO NOTHING RETURNING id""",
                (identifier,message.telegram_update_id,*key,mode,message.available_text,message.checksum,
                 source_id,permissions.version,message.occurred_at,window_days,public_confirmed,now,expiry),
            ).fetchone()
            return row[0] if row else None

    def claim_job(self, *, lease_seconds: int = 120, now=None) -> CoachJob | None:
        at = _now(now)
        with self.conn.transaction():
            self.conn.execute(
                "UPDATE coach_jobs SET status='pending',lease_token=NULL WHERE chat_id=%s AND message_thread_id=%s "
                "AND status='processing' AND lease_until<%s AND expires_at>%s",
                (self.chat_id,self.message_thread_id,at,at),
            )
            token = uuid4().hex
            row = self.conn.execute(
                """WITH candidate AS (SELECT id FROM coach_jobs WHERE chat_id=%s AND message_thread_id=%s
                   AND status='pending' AND expires_at>%s ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1)
                   UPDATE coach_jobs j SET status='processing',lease_token=%s,lease_until=%s,attempts=attempts+1
                   FROM candidate WHERE j.id=candidate.id
                   RETURNING j.id,j.chat_id,j.message_thread_id,j.author_user_id,j.telegram_update_id,
                     j.telegram_message_id,j.mode,j.current_text,j.source_checksum,j.source_id,j.consent_version,
                     j.lease_token,j.occurred_at,j.window_days,j.public_confirmed,j.recovery_attempt""",
                (self.chat_id,self.message_thread_id,at,token,at+timedelta(seconds=lease_seconds)),
            ).fetchone()
            return CoachJob(*row) if row else None

    def _job_current(self, job: CoachJob, *, now=None, lock_job: bool = False) -> bool:
        if not isinstance(job,CoachJob):
            return False
        if (job.chat_id,job.message_thread_id) != (self.chat_id,self.message_thread_id):
            return False
        permission = self.permissions(job.author_user_id)
        if permission.version != job.consent_version or not self._mode_allowed(permission,job.mode):
            return False
        row = self.conn.execute(
            """SELECT 1 FROM coach_jobs j JOIN coach_message_versions v USING(chat_id,message_thread_id,author_user_id,telegram_message_id)
               WHERE j.id=%s AND j.status='processing' AND j.lease_token=%s AND j.expires_at>%s
                 AND j.lease_until>%s AND v.checksum=%s AND j.source_checksum=%s
                 AND j.chat_id=%s AND j.message_thread_id=%s AND j.author_user_id=%s
                 AND j.telegram_update_id=%s AND j.telegram_message_id=%s AND j.mode=%s
                 AND j.current_text=%s AND j.consent_version=%s
                 AND j.source_id IS NOT DISTINCT FROM %s AND j.recovery_attempt=%s""" + (" FOR UPDATE OF j" if lock_job else ""),
            (job.id,job.lease_token,_now(now),_now(now),job.source_checksum,job.source_checksum,
             job.chat_id,job.message_thread_id,job.author_user_id,job.telegram_update_id,
             job.telegram_message_id,job.mode,job.current_text,job.consent_version,job.source_id,job.recovery_attempt),
        ).fetchone()
        return row is not None

    def job_is_current(self, job: CoachJob, *, now=None) -> bool:
        try:
            with self.author_lock(job.author_user_id,require_enabled=False):
                return self.scope_enabled() and self._job_current(job,now=now)
        except ArchivePermissionError:
            return False

    def complete_job(self, job: CoachJob, *, reason: str = "completed"):
        self.conn.execute(
            "UPDATE coach_jobs SET status='completed',current_text=NULL,source_checksum=NULL,source_id=NULL,lease_token=NULL,lease_until=NULL,reason=%s "
            "WHERE id=%s AND lease_token=%s AND recovery_attempt=%s AND status='processing'",
            (safe_decision_reason(reason),job.id,job.lease_token,job.recovery_attempt),
        )

    def fail_job(self, job: CoachJob, reason: str = "processing_failed"):
        safe_reason = reason if re.fullmatch(r"[a-z_]{1,60}",reason) else "processing_failed"
        self.conn.execute(
            "UPDATE coach_jobs SET status='failed',current_text=NULL,source_checksum=NULL,source_id=NULL,lease_token=NULL,lease_until=NULL,reason=%s "
            "WHERE id=%s AND lease_token=%s AND recovery_attempt=%s AND status='processing'",
            (safe_reason,job.id,job.lease_token,job.recovery_attempt),
        )

    def _proofs_valid(self, author: int, proofs: Sequence[SourceProof], *, current_source_id=None,
                      allow_current_only=False) -> bool:
        if not proofs or len(proofs)>200 or any(not isinstance(p,SourceProof) for p in proofs):
            return False
        if len({p.source_id for p in proofs}) != len(proofs):
            return False
        if any(not isinstance(p.source_id,str) or not isinstance(p.checksum,str) or not re.fullmatch(r"[a-f0-9]{64}",p.checksum)
               or type(p.access_version) is not int or p.access_version<=0 for p in proofs):
            return False
        rows = self.conn.execute(
            """SELECT r.source_id,r.checksum,c.version,c.historical_inference,r.contains_external_content
               FROM source_archive_revisions r JOIN source_archive_messages m
                 ON m.chat_id=r.chat_id AND m.telegram_message_id=r.telegram_message_id
               JOIN source_archive_consents c ON c.chat_id=r.chat_id AND c.message_thread_id=r.message_thread_id
                 AND c.author_user_id=r.author_user_id
               WHERE r.chat_id=%s AND r.message_thread_id=%s AND r.author_user_id=%s
                 AND r.source_id=ANY(%s) AND r.source_status='active' AND NOT m.is_deleted
                 AND m.latest_source_id=r.source_id AND c.retain_source""",
            (*self._key(author),[p.source_id for p in proofs]),
        ).fetchall()
        found = {row[0]:row for row in rows}
        for proof in proofs:
            row = found.get(proof.source_id)
            if not row or (row[1],row[2])!=(proof.checksum,proof.access_version):
                return False
            current = allow_current_only and proof.source_id==current_source_id
            if not current and (not row[3] or row[4]):
                return False
        return True

    def enqueue_output(self, job: CoachJob, text: str, *, source_proofs: Sequence[SourceProof] = (),
                       kind: str = "reply", experiment_id: str | None = None, now=None) -> str | None:
        identifiers=self.enqueue_outputs(job,(text,),source_proofs=source_proofs,kind=kind,
                                         experiment_id=experiment_id,now=now)
        return identifiers[0] if identifiers else None

    def enqueue_outputs(self, job: CoachJob, texts: Sequence[str], *, source_proofs: Sequence[SourceProof] = (),
                        kind: str = "reply", experiment_links: Sequence[tuple[str,int]] = (),
                        experiment_id: str | None = None, now=None) -> tuple[str,...]:
        """Queue an atomic ordered answer, with no fixed message-count quota."""
        if isinstance(texts,(str,bytes)):
            raise ValueError("An ordered sequence of Telegram messages is required")
        texts=tuple(texts)
        if not texts or any(not isinstance(t,str) or not t.strip() or len(t)>4096 for t in texts):
            raise ValueError("Every output must be nonempty and fit one Telegram message")
        links=tuple(experiment_links)+(((experiment_id,len(texts)-1),) if experiment_id else ())
        if any(not isinstance(p,(tuple,list)) or len(p)!=2 or not isinstance(p[0],str)
               or type(p[1]) is not int or not 0<=p[1]<len(texts) for p in links):
            raise ValueError("Experiment links require a valid zero-based message index")
        if len({p[0] for p in links})!=len(links):
            raise ValueError("An experiment belongs to exactly one delivered answer part")
        with self.author_lock(job.author_user_id,require_enabled=False):
            if not self.scope_enabled() or not self._job_current(job,now=now,lock_job=True):
                return ()
            if source_proofs and not self._proofs_valid(job.author_user_id,source_proofs,
                    current_source_id=job.source_id,allow_current_only=job.mode=="direct"):
                return ()
            # History-backed modes cannot silently strip their evidence.
            if job.mode == "index":
                return ()
            if job.mode in {"auto_report","weekly_review","track"} and not source_proofs and kind != "notice":
                return ()
            if links and (kind!="reply" or any(not self._prepared_experiment_valid(
                    job.author_user_id,identifier,source_proofs) for identifier,_ in links)):
                return ()
            if self.conn.execute("SELECT 1 FROM coaching_outputs WHERE job_id=%s LIMIT 1",(job.id,)).fetchone():
                return ()
            at = _now(now)
            job_expiry=self.conn.execute("SELECT expires_at FROM coach_jobs WHERE id=%s",(job.id,)).fetchone()[0]
            identifiers=[]
            for index,text in enumerate(texts):
                identifier=uuid4().hex
                part_experiments=tuple(item for item,part in links if part==index)
                self.conn.execute(
                    """INSERT INTO coaching_outputs(id,dedupe_key,job_id,chat_id,message_thread_id,author_user_id,
                        request_message_id,request_checksum,source_id,consent_version,mode,kind,text,source_proofs,
                        created_at,expires_at,experiment_id,bundle_id,part_index,origin_occurred_at,origin_update_id)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s)""",
                    (identifier,f"job:{job.id}:{index}",job.id,*self._key(job.author_user_id),job.telegram_message_id,
                     job.source_checksum,job.source_id,job.consent_version,job.mode,kind,text,_proof_json(source_proofs),
                     at,min(job_expiry,at+timedelta(seconds=self.transient_ttl_seconds)),
                     part_experiments[0] if part_experiments else None,"job:"+job.id,index,
                     job.occurred_at,job.telegram_update_id),
                )
                for experiment in part_experiments:
                    self.conn.execute("INSERT INTO coaching_output_experiments(output_id,experiment_id) VALUES(%s,%s)",
                                      (identifier,experiment))
                identifiers.append(identifier)
            return tuple(identifiers)

    def enqueue_control_output(self, normalized, text: str, *, disclosure=False, now=None) -> str | None:
        message = self.authenticated_message(normalized)
        if message is None or message.contains_external_content or message.is_edited:
            return None
        if not isinstance(text,str) or not text.strip() or len(text)>4096:
            raise ValueError("Invalid control reply")
        with self.author_lock(message.author_user_id, require_enabled=False):
            self.ensure_participant(message)
            at = _now(now)
            identifier=uuid4().hex
            row = self.conn.execute(
                """INSERT INTO coaching_outputs(id,dedupe_key,chat_id,message_thread_id,author_user_id,
                       request_message_id,mode,kind,text,disclosure,created_at,expires_at,bundle_id,origin_occurred_at,origin_update_id)
                   VALUES(%s,%s,%s,%s,%s,%s,'control','control',%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(dedupe_key) DO NOTHING RETURNING id""",
                (identifier,f"control:{message.telegram_update_id}",*self._key(message.author_user_id),
                 message.telegram_message_id,text,disclosure,at,at+timedelta(seconds=self.transient_ttl_seconds),identifier,
                 message.occurred_at,message.telegram_update_id),
            ).fetchone()
            return row[0] if row else None

    def claim_output(self, *, lease_seconds: int = 30, now=None) -> CoachOutput | None:
        at = _now(now)
        with self.conn.transaction():
            self.conn.execute(
                "UPDATE coaching_outputs SET status='uncertain',text=NULL,source_proofs='[]'::jsonb,request_checksum=NULL,source_id=NULL,lease_token=NULL "
                "WHERE chat_id=%s AND message_thread_id=%s AND status='sending' AND lease_until<%s",
                (self.chat_id,self.message_thread_id,at),
            )
            self._cancel_failed_bundle_tails(scope=(self.chat_id,self.message_thread_id))
            row = self.conn.execute(
                """WITH candidate AS (SELECT pending.id FROM coaching_outputs pending WHERE chat_id=%s AND message_thread_id=%s
                   AND status='pending' AND expires_at>%s AND NOT EXISTS (
                     SELECT 1 FROM coaching_outputs earlier WHERE earlier.bundle_id=pending.bundle_id
                       AND earlier.part_index<pending.part_index AND earlier.status<>'sent')
                   ORDER BY created_at,bundle_id,part_index FOR UPDATE SKIP LOCKED LIMIT 1)
                   UPDATE coaching_outputs o SET status='sending',lease_token=%s,lease_until=%s
                   FROM candidate WHERE o.id=candidate.id
                   RETURNING o.id,o.chat_id,o.message_thread_id,o.author_user_id,o.text,o.kind,o.mode,
                     o.source_proofs,o.consent_version,o.lease_token,o.job_id,o.request_message_id,
                     o.request_checksum,o.source_id,o.disclosure,o.experiment_id,o.bundle_id,o.part_index,
                     ARRAY(SELECT e.experiment_id FROM coaching_output_experiments e WHERE e.output_id=o.id ORDER BY e.experiment_id)""",
                (self.chat_id,self.message_thread_id,at,uuid4().hex,at+timedelta(seconds=lease_seconds)),
            ).fetchone()
            if not row:
                return None
            data = list(row)
            data[7] = tuple(SourceProof(**item) for item in data[7])
            data[-1]=tuple(data[-1])
            return CoachOutput(*data)

    def deliver_output(self, output: CoachOutput, sender_callback: Callable[[CoachOutput],dict], *,
                       now=None, on_sent: Callable[[CoachOutput,dict],None] | None = None) -> str:
        """Lock permission + exact sources through the short synchronous send.

        A revoke waits for an already-in-flight HTTP send, and blocks all future
        sends. Telegram cannot retract bytes already in flight. Sender exceptions
        are conservatively uncertain and are NEVER automatically retried.
        """
        if (output.chat_id,output.message_thread_id)!=(self.chat_id,self.message_thread_id):
            return "cancelled"
        try:
            with self.author_lock(output.author_user_id, require_enabled=output.mode != "control"):
                row = self.conn.execute(
                    "SELECT text,source_proofs,status,lease_token,expires_at,lease_until,chat_id,message_thread_id,author_user_id,"
                    "mode,kind,consent_version,request_message_id,request_checksum,source_id,job_id,disclosure,experiment_id,bundle_id,part_index "
                    "FROM coaching_outputs WHERE id=%s FOR UPDATE",
                    (output.id,),
                ).fetchone()
                if not row or row[2]!="sending" or row[3]!=output.lease_token:
                    return "cancelled"
                bound = (output.chat_id,output.message_thread_id,output.author_user_id,output.mode,output.kind,
                         output.consent_version,output.request_message_id,output.request_checksum,output.source_id,
                         output.job_id,output.disclosure,output.experiment_id,output.bundle_id,output.part_index)
                allowed = (row[4]>_now(now) and row[5]>_now(now) and row[0]==output.text
                           and tuple(row[6:])==bound and row[1]==json.loads(_proof_json(output.source_proofs)))
                experiments=tuple(r[0] for r in self.conn.execute(
                    "SELECT experiment_id FROM coaching_output_experiments WHERE output_id=%s ORDER BY experiment_id",(output.id,),
                ).fetchall())
                allowed=allowed and experiments==output.experiment_ids
                allowed=allowed and not self.conn.execute(
                    "SELECT 1 FROM coaching_outputs WHERE bundle_id=%s AND part_index<%s AND status<>'sent' LIMIT 1",
                    (output.bundle_id,output.part_index),
                ).fetchone()
                if output.mode!="control":
                    permission = self.permissions(output.author_user_id)
                    current = self.conn.execute(
                        "SELECT checksum,expires_at FROM coach_message_versions WHERE chat_id=%s AND message_thread_id=%s "
                        "AND author_user_id=%s AND telegram_message_id=%s",
                        (*self._key(output.author_user_id),output.request_message_id),
                    ).fetchone()
                    allowed = allowed and permission.version==output.consent_version and self._mode_allowed(permission,output.mode)
                    allowed = allowed and bool(current and current[0]==output.request_checksum and current[1]>_now(now))
                    if output.source_proofs:
                        allowed = allowed and self._proofs_valid(output.author_user_id,output.source_proofs,
                            current_source_id=output.source_id,allow_current_only=output.mode=="direct")
                    elif output.mode!="direct" and output.kind!="notice":
                        allowed = False
                    allowed=allowed and all(self._prepared_experiment_valid(
                        output.author_user_id,identifier,output.source_proofs) for identifier in experiments)
                if not allowed:
                    self._finish_output(output.id,"cancelled")
                    return "cancelled"
                try:
                    sent = sender_callback(output)
                except Exception:
                    self._finish_output(output.id,"uncertain")
                    return "uncertain"
                telegram_id = sent.get("message_id") if isinstance(sent,dict) else None
                if on_sent is not None:
                    try:
                        with self.conn.transaction():
                            on_sent(output,sent if isinstance(sent,dict) else {})
                    except Exception as exc:
                        logger.warning("Coach delivered-context recording failed: %s",type(exc).__name__)
                self._finish_output(output.id,"sent",telegram_id if type(telegram_id) is int else None)
                for identifier in experiments:
                    self.conn.execute(
                        "UPDATE coach_episodes SET status='offered' WHERE id=%s AND chat_id=%s "
                        "AND message_thread_id=%s AND author_user_id=%s AND status='prepared' AND is_active",
                        (identifier,*self._key(output.author_user_id)),
                    )
                if output.disclosure:
                    self.conn.execute(
                        "UPDATE coach_participant_state SET disclosure_version=1,disclosure_delivered_at=%s "
                        "WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s",
                        (_now(now),*self._key(output.author_user_id)),
                    )
                return "sent"
        except ArchivePermissionError:
            with self.conn.transaction():
                self._finish_output(output.id,"cancelled")
            return "cancelled"

    def _finish_output(self, identifier, status, telegram_id=None):
        row=self.conn.execute(
            "UPDATE coaching_outputs SET status=%s,text=NULL,source_proofs='[]'::jsonb,request_checksum=NULL,source_id=NULL,"
            "lease_token=NULL,lease_until=NULL,telegram_message_id=%s WHERE id=%s AND status IN ('pending','sending') RETURNING bundle_id",
            (status,telegram_id,identifier),
        ).fetchone()
        if status!="sent" and row:
            self._cancel_failed_bundle_tails(bundle_id=row[0])

    def _cancel_failed_bundle_tails(self, *, bundle_id=None, scope=None):
        cancel_failed_bundle_tails(self.conn, bundle_id=bundle_id, scope=scope)

    def cancel_output(self, output: CoachOutput, reason: str = "feature_disabled") -> bool:
        if (output.chat_id,output.message_thread_id)!=(self.chat_id,self.message_thread_id):
            return False
        with self.conn.transaction():
            row=self.conn.execute(
                "UPDATE coaching_outputs SET status='cancelled',text=NULL,source_proofs='[]'::jsonb,"
                "request_checksum=NULL,source_id=NULL,lease_token=NULL,lease_until=NULL "
                "WHERE id=%s AND lease_token=%s AND status='sending' RETURNING bundle_id",
                (output.id,output.lease_token),
            ).fetchone()
            if row:
                self._cancel_failed_bundle_tails(bundle_id=row[0])
            return row is not None

    def _prepared_experiment_valid(self, author_user_id: int, experiment_id: str,
                                   output_proofs: Sequence[SourceProof]) -> bool:
        if not isinstance(experiment_id,str) or not re.fullmatch(r"[a-f0-9]{32}",experiment_id):
            return False
        row=self.conn.execute(
            "SELECT source_proofs FROM coach_episodes WHERE id=%s AND chat_id=%s AND message_thread_id=%s "
            "AND author_user_id=%s AND episode_type='experiment' AND status='prepared' AND is_active FOR UPDATE",
            (experiment_id,*self._key(author_user_id)),
        ).fetchone()
        if not row:
            return False
        proofs=self._refresh_episode_proofs(author_user_id,tuple(SourceProof(**p) for p in row[0]))
        # A prepared proposal cannot add evidence outside the final response packet.
        return bool(proofs) and set(proofs).issubset(set(output_proofs))

    def save_episode(self, *, author_user_id: int, episode_type: str, summary: str,
                     source_proofs: Sequence[SourceProof], dedupe_key: str | None = None,
                     status: str | None = None, originating_job: CoachJob | None = None) -> str | None:
        # An engine-side check is only a snapshot. The originating lease must
        # still be current under the exact lock used by delete/revoke/feedback.
        if not isinstance(originating_job,CoachJob) or originating_job.author_user_id!=author_user_id:
            return None
        if not isinstance(summary,str) or not summary.strip() or len(summary)>2000:
            raise ValueError("A bounded source-backed episode summary is required")
        if episode_type not in {"goal","plan","attempt","action","result","experiment","observation","commitment","decision"}:
            raise ValueError("Unknown episode type")
        status={"proposed":"prepared","completed":"tried_self_reported","abandoned":"retired"}.get(status,status)
        if status is not None and status not in {"prepared","offered","accepted","declined","tried_self_reported","not_tried_self_reported","retired"}:
            raise ValueError("Unknown experiment status")
        with self.author_lock(author_user_id,require_enabled=False):
            if not self.scope_enabled() or not self._job_current(originating_job,lock_job=True):
                return None
            if not self._proofs_valid(author_user_id,source_proofs):
                return None
            proof_json = _proof_json(source_proofs)
            identity=json.dumps(sorted((p.source_id,p.checksum) for p in source_proofs),separators=(",",":"))
            dedupe_key = dedupe_key or hashlib.sha256((episode_type+summary+identity).encode()).hexdigest()
            if len(dedupe_key)>512:
                raise ValueError("Episode dedupe key is too long")
            if episode_type=="experiment" and status in {"prepared","offered"}:
                existing=self.conn.execute(
                    "SELECT id FROM coach_episodes WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s "
                    "AND dedupe_key=%s AND is_active AND status<>'prepared'",(*self._key(author_user_id),dedupe_key),
                ).fetchone()
                if existing:
                    return existing[0]  # Re-indexing cannot reset a participant's feedback or remove its source.
            observed = self.conn.execute("SELECT MAX(occurred_at) FROM source_archive_revisions WHERE source_id=ANY(%s)",
                                         ([p.source_id for p in source_proofs],)).fetchone()[0]
            row = self.conn.execute(
                """INSERT INTO coach_episodes(id,chat_id,message_thread_id,author_user_id,episode_type,summary,source_proofs,dedupe_key,status,observed_at)
                   VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
                   ON CONFLICT(chat_id,message_thread_id,author_user_id,dedupe_key) DO UPDATE
                   SET summary=EXCLUDED.summary,source_proofs=EXCLUDED.source_proofs,is_active=TRUE,
                       status=CASE WHEN coach_episodes.episode_type='experiment' AND coach_episodes.is_active
                          AND EXCLUDED.status IN ('prepared','offered') AND coach_episodes.status<>'prepared'
                          THEN coach_episodes.status ELSE EXCLUDED.status END,
                       observed_at=EXCLUDED.observed_at RETURNING id""",
                (uuid4().hex,*self._key(author_user_id),episode_type,summary,proof_json,dedupe_key,status,observed),
            ).fetchone()
            return row[0]

    def list_episodes(self, *, author_user_id: int, limit: int = 50, since=None, until=None,
                      episode_type: str | None = None) -> list[dict]:
        if type(limit) is not int or not 1<=limit<=200:
            raise ValueError("Episode page limit must be 1..200")
        with self.author_lock(author_user_id):
            permission = self.permissions(author_user_id)
            if not (permission.retain_source and permission.historical_inference):
                return []
            extra,params = "",self._key(author_user_id)
            if episode_type is not None:
                extra += " AND episode_type=%s"; params += (episode_type,)
            if since is not None:
                extra += " AND observed_at>=%s"; params += (_now(since),)
            if until is not None:
                extra += " AND observed_at<%s"; params += (_now(until),)
            rows = self.conn.execute(
                "SELECT id,episode_type,summary,source_proofs,created_at,observed_at,status FROM coach_episodes "
                "WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s AND is_active "
                "AND status IS DISTINCT FROM 'prepared'"+extra+
                " ORDER BY observed_at DESC,id LIMIT %s",(*params,limit),
            ).fetchall()
            result=[]
            for row in rows:
                proofs=self._refresh_episode_proofs(author_user_id,tuple(SourceProof(**p) for p in row[3]))
                if proofs:
                    result.append(dict(zip(("id","episode_type","summary","source_proofs","created_at","observed_at","status"),
                                           (*row[:3],proofs,*row[4:]))))
                else:
                    self.conn.execute("UPDATE coach_episodes SET summary=NULL,source_proofs='[]'::jsonb,is_active=FALSE WHERE id=%s",(row[0],))
            return result

    def _refresh_episode_proofs(self, author_user_id: int, proofs: Sequence[SourceProof]):
        """Unrelated output-flag changes do not erase still-authorized history.

        History/retention revocation already scrubs summaries in the DB trigger.
        Each surviving record is freshly authorized and rebound to today's grant
        version; old queued reply snapshots remain invalid.
        """
        if not proofs:
            return ()
        version=self.permissions(author_user_id).version
        current=tuple(SourceProof(p.source_id,p.checksum,version) for p in proofs)
        return current if self._proofs_valid(author_user_id,current) else ()

    def save_experiment(self, *, author_user_id: int, proposal: str, source_proofs: Sequence[SourceProof],
                        status="prepared", originating_job: CoachJob | None = None):
        return self.save_episode(author_user_id=author_user_id,episode_type="experiment",summary=proposal,
                                 source_proofs=source_proofs,status=status,originating_job=originating_job)

    def list_experiments(self, *, author_user_id: int, limit: int = 20, since=None, until=None):
        episodes=self.list_episodes(author_user_id=author_user_id,limit=limit,since=since,until=until,episode_type="experiment")
        return [{**item,"proposal":item["summary"]} for item in episodes]

    def set_experiment_status(self, *, author_user_id: int, experiment_id: str, status: str,
                              source_proofs: Sequence[SourceProof]) -> bool:
        status={"completed":"tried_self_reported","abandoned":"retired"}.get(status,status)
        if status not in {"accepted","declined","tried_self_reported","not_tried_self_reported","retired"}:
            raise ValueError("Unknown experiment status")
        with self.author_lock(author_user_id):
            row=self.conn.execute(
                "SELECT source_proofs FROM coach_episodes WHERE id=%s AND chat_id=%s AND message_thread_id=%s "
                "AND author_user_id=%s AND episode_type='experiment' AND is_active AND status<>'prepared' FOR UPDATE",
                (experiment_id,*self._key(author_user_id)),
            ).fetchone()
            if not row or not self._proofs_valid(author_user_id,source_proofs):
                return False
            proposal_proofs=self._refresh_episode_proofs(author_user_id,tuple(SourceProof(**p) for p in row[0]))
            if not proposal_proofs:
                return False
            merged={p.source_id:p for p in [*proposal_proofs,*source_proofs]}
            if not self._proofs_valid(author_user_id,tuple(merged.values())):
                return False
            observed=self.conn.execute("SELECT MAX(occurred_at) FROM source_archive_revisions WHERE source_id=ANY(%s)",
                                       (list(merged),)).fetchone()[0]
            self.conn.execute("UPDATE coach_episodes SET status=%s,source_proofs=%s::jsonb,observed_at=%s WHERE id=%s",
                              (status,_proof_json(tuple(merged.values())),observed,experiment_id))
            # Same permission lock as final delivery: prepared answers with the
            # previous self-reported state cannot leave after this feedback.
            self.conn.execute(
                "UPDATE coach_jobs SET status='cancelled',current_text=NULL,source_checksum=NULL,source_id=NULL,"
                "lease_token=NULL,lease_until=NULL,reason='experiment_feedback' "
                "WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s "
                "AND status IN ('pending','processing')",self._key(author_user_id),
            )
            self.conn.execute(
                "UPDATE coaching_outputs SET status='cancelled',text=NULL,source_proofs='[]'::jsonb,"
                "request_checksum=NULL,source_id=NULL,lease_token=NULL,lease_until=NULL "
                "WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s "
                "AND mode<>'control' AND status IN ('pending','sending')",self._key(author_user_id),
            )
            return True

    def record_experiment_feedback(self, message: ArchiveMessage, *, experiment_id: str, status: str) -> bool:
        """Identity comes from the authenticated command; feedback needs its own source."""
        if (message.chat_id,message.message_thread_id)!=(self.chat_id,self.message_thread_id):
            return False
        with self.author_lock(message.author_user_id):
            permission=self.permissions(message.author_user_id)
            proof=SourceProof(message.source_id,message.checksum,permission.version)
            return self.set_experiment_status(author_user_id=message.author_user_id,experiment_id=experiment_id,
                                               status=status,source_proofs=[proof])

    def feedback_candidates(self, message: ArchiveMessage) -> tuple[str,...]:
        """Resolve only this participant's delivered experiments, never another person's reply."""
        with self.author_lock(message.author_user_id):
            params=self._key(message.author_user_id)
            rows=[]
            if message.reply_to_message_id is not None:
                rows=self.conn.execute(
                    "SELECT e.id,e.source_proofs FROM coach_episodes e JOIN coaching_output_experiments l ON l.experiment_id=e.id "
                    "JOIN coaching_outputs o ON o.id=l.output_id WHERE e.chat_id=%s AND e.message_thread_id=%s "
                    "AND e.author_user_id=%s AND e.is_active AND e.status NOT IN ('prepared','retired') "
                    "AND o.status='sent' AND o.telegram_message_id=%s ORDER BY e.id",
                    (*params,message.reply_to_message_id),
                ).fetchall()
            if not rows:
                rows=self.conn.execute(
                    "SELECT id,source_proofs FROM coach_episodes WHERE chat_id=%s AND message_thread_id=%s "
                    "AND author_user_id=%s AND episode_type='experiment' AND is_active "
                    "AND status IN ('offered','accepted') ORDER BY observed_at DESC,id",params,
                ).fetchall()
            return tuple(row[0] for row in rows if self._refresh_episode_proofs(
                message.author_user_id,tuple(SourceProof(**p) for p in row[1])))

    def maintenance(self, *, now=None) -> dict[str,int]:
        """Scrub all expired transient payloads in this explicitly bound bot DB.

        Cleanup also covers topics removed from the runtime configuration; it
        neither retrieves their contents nor changes retained source history.
        """
        return CoachScopeRepository(self.conn, bot_user_id=self.bot_user_id).maintenance(
            transient_ttl_seconds=self.transient_ttl_seconds, now=now,
        )
