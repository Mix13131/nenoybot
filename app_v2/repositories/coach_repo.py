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
import re
from typing import Callable, Sequence
from uuid import uuid4

from app_v2.adapters.telegram_archive import archive_message_from_update
from app_v2.domain.coach import CoachJob, CoachOutput, CoachPermissions, GRANT_FIELDS, SourceProof
from app_v2.domain.source_archive import ArchiveMessage
from app_v2.repositories.source_archive_repo import ArchivePermissionError, SourceArchiveRepository


class CoachConfigurationError(RuntimeError):
    pass


def _now(value=None):
    value = value or datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("A timezone-aware clock is required")
    return value.astimezone(timezone.utc)


def _proof_json(proofs):
    return json.dumps([asdict(item) for item in proofs], sort_keys=True, separators=(",", ":"))


class CoachRepository:
    def __init__(self, conn, *, chat_id: int, message_thread_id: int, bot_user_id: int,
                 transient_ttl_seconds: int = 86400):
        if type(chat_id) is not int or chat_id >= 0:
            raise ValueError("A negative Telegram group ID is required")
        if any(type(v) is not int or v <= 0 for v in (message_thread_id, bot_user_id)):
            raise ValueError("Explicit topic and bot IDs are required")
        if type(transient_ttl_seconds) is not int or not 60 <= transient_ttl_seconds <= 86400:
            raise ValueError("Transient text lifetime must be between one minute and 24 hours")
        self.conn, self.chat_id, self.message_thread_id = conn, chat_id, message_thread_id
        self.bot_user_id, self.transient_ttl_seconds = bot_user_id, transient_ttl_seconds
        self.archive = SourceArchiveRepository(conn)

    def _key(self, author):
        if type(author) is not int or author <= 0:
            raise ValueError("A trusted individual author ID is required")
        return self.chat_id, self.message_thread_id, author

    def bind_identity(self):
        """Explicit setup only; refuse a populated legacy bot database."""
        with self.conn.transaction():
            self.conn.execute("SELECT pg_advisory_xact_lock(1313162111)")
            existing = self.conn.execute("SELECT bot_user_id FROM coach_runtime_identity WHERE singleton").fetchone()
            if existing:
                if existing[0] != self.bot_user_id:
                    raise CoachConfigurationError("This database belongs to another Telegram bot")
                return
            occupied = self.conn.execute(
                "SELECT EXISTS(SELECT 1 FROM events) OR EXISTS(SELECT 1 FROM messages) "
                "OR EXISTS(SELECT 1 FROM outbox) OR EXISTS(SELECT 1 FROM memory_cards) "
                "OR EXISTS(SELECT 1 FROM chats) OR EXISTS(SELECT 1 FROM users)"
            ).fetchone()[0]
            if occupied:
                raise CoachConfigurationError("An isolated empty legacy queue/database is required")
            self.conn.execute("INSERT INTO coach_runtime_identity(singleton,bot_user_id) VALUES(TRUE,%s)",
                              (self.bot_user_id,))

    def assert_identity(self):
        row = self.conn.execute("SELECT bot_user_id FROM coach_runtime_identity WHERE singleton").fetchone()
        if not row or row[0] != self.bot_user_id:
            raise CoachConfigurationError("The explicitly bound bot identity does not match")

    def scope_enabled(self) -> bool:
        row=self.conn.execute("SELECT enabled FROM source_archive_scopes WHERE chat_id=%s AND message_thread_id=%s",
                              (self.chat_id,self.message_thread_id)).fetchone()
        return bool(row and row[0])

    def authenticated_message(self, normalized):
        message = archive_message_from_update(normalized)
        if message is None or (message.chat_id, message.message_thread_id) != (self.chat_id, self.message_thread_id):
            return None
        return message

    @contextmanager
    def author_lock(self, author_user_id: int, *, require_enabled: bool = True):
        """Same lock order as archive capture: scope then individual consent."""
        key = self._key(author_user_id)
        with self.conn.transaction():
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

    def permissions(self, author_user_id: int) -> CoachPermissions:
        row = self.conn.execute(
            """SELECT c.version,c.retain_source,c.historical_inference,c.auto_report_analysis,
                      c.unsolicited_group_reply,c.direct_request_group_reply,
                      COALESCE(s.track_status,'off'),COALESCE(s.disclosure_version=1 AND
                          s.disclosure_delivered_at IS NOT NULL,FALSE)
               FROM source_archive_consents c LEFT JOIN coach_participant_state s
                 USING(chat_id,message_thread_id,author_user_id)
               WHERE c.chat_id=%s AND c.message_thread_id=%s AND c.author_user_id=%s""",
            self._key(author_user_id),
        ).fetchone()
        return CoachPermissions(*row) if row else CoachPermissions()

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
            receipt = self.archive.forget_sources(
                chat_id=self.chat_id,message_thread_id=self.message_thread_id,
                author_user_id=message.author_user_id,actor_user_id=message.author_user_id,
            )
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
            if not current or (message.revision_at,message.telegram_update_id) <= tuple(current[:2]):
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
            granted_at=self.conn.execute(
                "SELECT updated_at FROM source_archive_consents WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s",
                self._key(message.author_user_id),
            ).fetchone()[0]
            boundary=granted_at.replace(microsecond=0)+(timedelta(seconds=1) if granted_at.microsecond else timedelta())
            if message.occurred_at<boundary:
                return None  # A later opt-in never authorizes a delayed earlier request.
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
                     j.lease_token,j.occurred_at,j.window_days,j.public_confirmed""",
                (self.chat_id,self.message_thread_id,at,token,at+timedelta(seconds=lease_seconds)),
            ).fetchone()
            return CoachJob(*row) if row else None

    def _job_current(self, job: CoachJob, *, now=None) -> bool:
        if (job.chat_id,job.message_thread_id) != (self.chat_id,self.message_thread_id):
            return False
        permission = self.permissions(job.author_user_id)
        if permission.version != job.consent_version or not self._mode_allowed(permission,job.mode):
            return False
        row = self.conn.execute(
            """SELECT 1 FROM coach_jobs j JOIN coach_message_versions v USING(chat_id,message_thread_id,author_user_id,telegram_message_id)
               WHERE j.id=%s AND j.status='processing' AND j.lease_token=%s AND j.expires_at>%s
                 AND j.lease_until>%s AND v.checksum=%s""",
            (job.id,job.lease_token,_now(now),_now(now),job.source_checksum),
        ).fetchone()
        return row is not None

    def job_is_current(self, job: CoachJob, *, now=None) -> bool:
        with self.author_lock(job.author_user_id,require_enabled=False):
            return self.scope_enabled() and self._job_current(job,now=now)

    def complete_job(self, job: CoachJob):
        self.conn.execute(
            "UPDATE coach_jobs SET status='completed',current_text=NULL,source_checksum=NULL,source_id=NULL,lease_token=NULL,lease_until=NULL "
            "WHERE id=%s AND lease_token=%s AND status='processing'",(job.id,job.lease_token),
        )

    def fail_job(self, job: CoachJob, reason: str = "processing_failed"):
        safe_reason = reason if re.fullmatch(r"[a-z_]{1,60}",reason) else "processing_failed"
        self.conn.execute(
            "UPDATE coach_jobs SET status='failed',current_text=NULL,source_checksum=NULL,source_id=NULL,lease_token=NULL,lease_until=NULL,reason=%s "
            "WHERE id=%s AND lease_token=%s AND status='processing'",(safe_reason,job.id,job.lease_token),
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
        if not isinstance(text,str) or not text.strip() or len(text)>4096:
            raise ValueError("A nonempty Telegram-sized output is required")
        with self.author_lock(job.author_user_id,require_enabled=False):
            if not self.scope_enabled() or not self._job_current(job,now=now):
                return None
            if source_proofs and not self._proofs_valid(job.author_user_id,source_proofs,
                    current_source_id=job.source_id,allow_current_only=job.mode=="direct"):
                return None
            # History-backed modes cannot silently strip their evidence.
            if job.mode == "index":
                return None
            if job.mode in {"weekly_review","track"} and not job.public_confirmed and kind != "notice":
                return None
            if job.mode in {"auto_report","weekly_review","track"} and not source_proofs and kind != "notice":
                return None
            if experiment_id is not None and (kind!="reply" or not self._prepared_experiment_valid(
                    job.author_user_id,experiment_id,source_proofs)):
                return None
            at = _now(now)
            job_expiry=self.conn.execute("SELECT expires_at FROM coach_jobs WHERE id=%s",(job.id,)).fetchone()[0]
            row = self.conn.execute(
                """INSERT INTO coaching_outputs(id,dedupe_key,job_id,chat_id,message_thread_id,author_user_id,
                    request_message_id,request_checksum,source_id,consent_version,mode,kind,text,source_proofs,created_at,expires_at,experiment_id)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
                    ON CONFLICT(dedupe_key) DO NOTHING RETURNING id""",
                (uuid4().hex,"job:"+job.id,job.id,*self._key(job.author_user_id),job.telegram_message_id,
                 job.source_checksum,job.source_id,job.consent_version,job.mode,kind,text,_proof_json(source_proofs),
                 at,min(job_expiry,at+timedelta(seconds=self.transient_ttl_seconds)),experiment_id),
            ).fetchone()
            return row[0] if row else None

    def enqueue_control_output(self, normalized, text: str, *, disclosure=False, now=None) -> str | None:
        message = self.authenticated_message(normalized)
        if message is None or message.contains_external_content or message.is_edited:
            return None
        if not isinstance(text,str) or not text.strip() or len(text)>4096:
            raise ValueError("Invalid control reply")
        with self.author_lock(message.author_user_id, require_enabled=False):
            self.ensure_participant(message)
            at = _now(now)
            row = self.conn.execute(
                """INSERT INTO coaching_outputs(id,dedupe_key,chat_id,message_thread_id,author_user_id,
                       request_message_id,mode,kind,text,disclosure,created_at,expires_at)
                   VALUES(%s,%s,%s,%s,%s,%s,'control','control',%s,%s,%s,%s)
                   ON CONFLICT(dedupe_key) DO NOTHING RETURNING id""",
                (uuid4().hex,f"control:{message.telegram_update_id}",*self._key(message.author_user_id),
                 message.telegram_message_id,text,disclosure,at,at+timedelta(seconds=self.transient_ttl_seconds)),
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
            row = self.conn.execute(
                """WITH candidate AS (SELECT id FROM coaching_outputs WHERE chat_id=%s AND message_thread_id=%s
                   AND status='pending' AND expires_at>%s ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1)
                   UPDATE coaching_outputs o SET status='sending',lease_token=%s,lease_until=%s
                   FROM candidate WHERE o.id=candidate.id
                   RETURNING o.id,o.chat_id,o.message_thread_id,o.author_user_id,o.text,o.kind,o.mode,
                     o.source_proofs,o.consent_version,o.lease_token,o.job_id,o.request_message_id,
                     o.request_checksum,o.source_id,o.disclosure,o.experiment_id""",
                (self.chat_id,self.message_thread_id,at,uuid4().hex,at+timedelta(seconds=lease_seconds)),
            ).fetchone()
            if not row:
                return None
            data = list(row)
            data[7] = tuple(SourceProof(**item) for item in data[7])
            return CoachOutput(*data)

    def deliver_output(self, output: CoachOutput, sender_callback: Callable[[CoachOutput],dict], *, now=None) -> str:
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
                    "mode,kind,consent_version,request_message_id,request_checksum,source_id,job_id,disclosure,experiment_id "
                    "FROM coaching_outputs WHERE id=%s FOR UPDATE",
                    (output.id,),
                ).fetchone()
                if not row or row[2]!="sending" or row[3]!=output.lease_token:
                    return "cancelled"
                bound = (output.chat_id,output.message_thread_id,output.author_user_id,output.mode,output.kind,
                         output.consent_version,output.request_message_id,output.request_checksum,output.source_id,
                         output.job_id,output.disclosure,output.experiment_id)
                allowed = (row[4]>_now(now) and row[5]>_now(now) and row[0]==output.text
                           and tuple(row[6:])==bound and row[1]==json.loads(_proof_json(output.source_proofs)))
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
                    if output.mode in {"weekly_review","track"} and output.kind!="notice":
                        confirmed=self.conn.execute("SELECT public_confirmed FROM coach_jobs WHERE id=%s",(output.job_id,)).fetchone()
                        allowed=allowed and bool(confirmed and confirmed[0])
                    if output.experiment_id is not None:
                        allowed=allowed and self._prepared_experiment_valid(
                            output.author_user_id,output.experiment_id,output.source_proofs)
                if not allowed:
                    self._finish_output(output.id,"cancelled")
                    return "cancelled"
                try:
                    sent = sender_callback(output)
                except Exception:
                    self._finish_output(output.id,"uncertain")
                    return "uncertain"
                telegram_id = sent.get("message_id") if isinstance(sent,dict) else None
                self._finish_output(output.id,"sent",telegram_id if type(telegram_id) is int else None)
                if output.experiment_id is not None:
                    self.conn.execute(
                        "UPDATE coach_episodes SET status='offered' WHERE id=%s AND chat_id=%s "
                        "AND message_thread_id=%s AND author_user_id=%s AND status='prepared' AND is_active",
                        (output.experiment_id,*self._key(output.author_user_id)),
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
        self.conn.execute(
            "UPDATE coaching_outputs SET status=%s,text=NULL,source_proofs='[]'::jsonb,request_checksum=NULL,source_id=NULL,"
            "lease_token=NULL,lease_until=NULL,telegram_message_id=%s WHERE id=%s AND status IN ('pending','sending')",
            (status,telegram_id,identifier),
        )

    def cancel_output(self, output: CoachOutput, reason: str = "feature_disabled") -> bool:
        if (output.chat_id,output.message_thread_id)!=(self.chat_id,self.message_thread_id):
            return False
        with self.conn.transaction():
            return self.conn.execute(
                "UPDATE coaching_outputs SET status='cancelled',text=NULL,source_proofs='[]'::jsonb,"
                "request_checksum=NULL,source_id=NULL,lease_token=NULL,lease_until=NULL "
                "WHERE id=%s AND lease_token=%s AND status='sending'",
                (output.id,output.lease_token),
            ).rowcount==1

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
                     status: str | None = None) -> str | None:
        if not isinstance(summary,str) or not summary.strip() or len(summary)>2000:
            raise ValueError("A bounded source-backed episode summary is required")
        if episode_type not in {"goal","plan","attempt","action","result","experiment","observation","commitment","decision"}:
            raise ValueError("Unknown episode type")
        status={"proposed":"prepared","completed":"tried_self_reported","abandoned":"retired"}.get(status,status)
        if status is not None and status not in {"prepared","offered","accepted","declined","tried_self_reported","not_tried_self_reported","retired"}:
            raise ValueError("Unknown experiment status")
        with self.author_lock(author_user_id):
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

    def save_experiment(self, *, author_user_id: int, proposal: str, source_proofs: Sequence[SourceProof], status="prepared"):
        return self.save_episode(author_user_id=author_user_id,episode_type="experiment",summary=proposal,
                                 source_proofs=source_proofs,status=status)

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

    def maintenance(self, *, now=None) -> dict[str,int]:
        """Scrub all expired transient payloads in this explicitly bound bot DB.

        Cleanup also covers topics removed from the runtime configuration; it
        neither retrieves their contents nor changes retained source history.
        """
        at=_now(now)
        with self.conn.transaction():
            self.assert_identity()
            jobs=self.conn.execute(
                "UPDATE coach_jobs SET status='expired',current_text=NULL,source_checksum=NULL,source_id=NULL,lease_token=NULL,lease_until=NULL "
                "WHERE expires_at<=%s AND status IN ('pending','processing')",(at,),
            ).rowcount
            outputs=self.conn.execute(
                "UPDATE coaching_outputs SET status=CASE WHEN status='sending' THEN 'uncertain' ELSE 'expired' END,"
                "text=NULL,source_proofs='[]'::jsonb,request_checksum=NULL,source_id=NULL,lease_token=NULL,lease_until=NULL "
                "WHERE (expires_at<=%s AND status IN ('pending','sending')) "
                "OR (status='sending' AND lease_until<%s)",(at,at),
            ).rowcount
            self.conn.execute("UPDATE coach_message_versions SET checksum=NULL WHERE expires_at<=%s",(at,))
            self.conn.execute("DELETE FROM coach_ingest_receipts WHERE received_at<%s",(at-timedelta(days=30),))
            self.conn.execute(
                "UPDATE coach_episodes SET summary=NULL,source_proofs='[]'::jsonb,is_active=FALSE "
                "WHERE status='prepared' AND is_active AND created_at<%s",
                (at-timedelta(seconds=self.transient_ttl_seconds),),
            )
            return {"expired_jobs":jobs,"expired_or_uncertain_outputs":outputs}
