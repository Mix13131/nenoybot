"""One explicit operator retry of a completed, wholly unresponded direct job.

Preview is a read-only transaction. Apply restores only the exact current live
source to the existing job and preserves its original Telegram ID and expiry.
No provider/Telegram call or paid-operation mutation is made here. The worker
uses ``coach-job:<same-id>:recovery:1`` for the one new, audited operation.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timedelta, timezone
import hashlib

from app_v2.adapters.postgres import connect
from app_v2.domain.source_archive import ArchiveMessage
from app_v2.repositories.coach_repo import CoachRepository
from app_v2.repositories.coach_scope_repo import CoachScopeRepository
from app_v2.repositories.source_archive_repo import ArchivePermissionError


class CoachRecoveryError(ValueError):
    """Machine reasons only; no source text, identities or credentials."""


@dataclass(frozen=True)
class _Job:
    id: str
    chat_id: int
    message_thread_id: int
    author_user_id: int
    telegram_update_id: int
    telegram_message_id: int
    status: str
    recovery_attempt: int
    consent_version: int
    occurred_at: datetime
    created_at: datetime
    expires_at: datetime


_JOB_COLUMNS = ",".join(field.name for field in fields(_Job))
_SOURCE_NAMES = tuple(field.name for field in fields(ArchiveMessage))
_SOURCE_COLUMNS = ",".join("r." + name for name in _SOURCE_NAMES)


def _utc(value, label: str) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise CoachRecoveryError("invalid_" + label)
    return value.astimezone(timezone.utc)


def _summary(*, dry_run: bool, seen: int, reason: str, eligible: bool = False,
             rearmed: bool = False, already: bool = False) -> dict:
    return {
        "status": "preview" if dry_run else "rearmed" if rearmed else "unchanged",
        "dry_run": dry_run,
        "reason": reason,
        "counts": {
            "candidates_seen": seen, "maximum_jobs": 1, "eligible": int(eligible),
            "rearmed": int(rearmed), "already_recovered": int(already),
            "blocked": int(not eligible and not already and seen > 0),
            "ai_calls_made": 0, "telegram_messages_sent": 0,
        },
    }


def _paid_prefix(job_id: str, *, recovery: bool = False) -> str:
    operation = "coach-job:" + job_id + (":recovery:1" if recovery else "")
    return "coach:" + hashlib.sha256(operation.encode("utf-8")).hexdigest() + ":direct:"


def _source_if_eligible(conn, config, job: _Job, at: datetime) -> tuple[ArchiveMessage | None, str]:
    if job.status != "completed":
        return None, "job_not_completed"
    if not config.permits_scope(job.chat_id, job.message_thread_id):
        return None, "scope_not_allowed"
    if job.expires_at <= at or job.created_at > at or job.occurred_at > at:
        return None, "request_expired_or_future"
    # Any output for this request is a hard stop, including cancelled, uncertain
    # and already-sent rows and control outputs whose job_id is NULL.
    outputs = conn.execute(
        "SELECT 1 FROM coaching_outputs WHERE job_id=%s OR "
        "(chat_id=%s AND message_thread_id=%s AND author_user_id=%s AND request_message_id=%s) LIMIT 1",
        (job.id, job.chat_id, job.message_thread_id, job.author_user_id, job.telegram_message_id),
    ).fetchone()
    if outputs:
        return None, "output_already_exists"
    group = conn.execute(
        "SELECT active,active_since,active_update_id,changed_at,membership_update_id "
        "FROM coach_group_memberships WHERE chat_id=%s", (job.chat_id,),
    ).fetchone()
    if not group or not group[0]:
        return None, "group_inactive_or_unknown"
    # The original job predates recovery fields. Never infer an old membership
    # epoch: reject every known lifecycle change later than this request.
    if (group[1] > job.occurred_at or group[3] > job.occurred_at
            or (group[1] == job.occurred_at and group[2] is not None
                and group[2] >= job.telegram_update_id)
            or (group[3] == job.occurred_at and group[4] is not None
                and group[4] >= job.telegram_update_id)):
        return None, "group_membership_changed"
    scope = conn.execute(
        "SELECT enabled,activated_at FROM source_archive_scopes WHERE chat_id=%s AND message_thread_id=%s",
        (job.chat_id, job.message_thread_id),
    ).fetchone()
    if not scope or not scope[0] or scope[1] > job.occurred_at:
        return None, "scope_disabled_or_changed"
    key = (job.chat_id, job.message_thread_id, job.author_user_id)
    grant = conn.execute(
        "SELECT version,retain_source,direct_request_group_reply,retain_since FROM source_archive_consents "
        "WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s", key,
    ).fetchone()
    if (not grant or grant[0] != job.consent_version or not grant[1] or not grant[2]
            or grant[3] is None or grant[3] > job.occurred_at):
        return None, "permissions_changed"
    if conn.execute(
        "SELECT 1 FROM source_archive_deletions WHERE chat_id=%s AND message_thread_id=%s "
        "AND author_user_id=%s AND telegram_message_id=%s", (*key, job.telegram_message_id),
    ).fetchone():
        return None, "source_deleted"
    row = conn.execute(
        "SELECT m.is_deleted,r.source_id,r.source_status,r.source_origin,r.checksum," + _SOURCE_COLUMNS +
        " FROM source_archive_messages m LEFT JOIN source_archive_revisions r ON r.source_id=m.latest_source_id "
        "WHERE m.chat_id=%s AND m.telegram_message_id=%s", (job.chat_id, job.telegram_message_id),
    ).fetchone()
    if not row or row[0] or row[2] != "active" or row[3] != "telegram_update":
        return None, "source_unavailable"
    try:
        values = dict(zip(_SOURCE_NAMES, row[5:]))
        # PostgreSQL renders timestamptz in the connection's timezone. Restore
        # the same instants to the canonical UTC representation used by capture.
        for name in ("occurred_at", "revision_at"):
            values[name] = _utc(values[name], "source_" + name)
        source = ArchiveMessage(**values)
    except (ValueError, TypeError):
        return None, "source_invalid"
    if ((source.chat_id, source.message_thread_id, source.author_user_id,
         source.telegram_message_id, source.telegram_update_id, source.occurred_at) !=
            (*key, job.telegram_message_id, job.telegram_update_id, job.occurred_at)
            or source.is_edited or source.revision_at != job.occurred_at
            or source.contains_external_content or not source.available_text
            or source.source_id != row[1] or source.checksum != row[4]):
        return None, "source_changed"
    version = conn.execute(
        "SELECT telegram_update_id,revision_at,checksum,expires_at FROM coach_message_versions "
        "WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s AND telegram_message_id=%s",
        (*key, job.telegram_message_id),
    ).fetchone()
    if (not version or version[:3] != (job.telegram_update_id, source.revision_at, source.checksum)
            or version[3] <= at):
        return None, "message_version_changed_or_expired"
    prefix = _paid_prefix(job.id)
    operations = conn.execute(
        "SELECT operation_id,purpose,status,outcome,reconciled_at FROM coach_ai_operations "
        "WHERE operation_id LIKE %s ORDER BY operation_id LIMIT 2", (prefix + "%",),
    ).fetchall()
    if len(operations) != 1:
        return None, "original_operation_not_assessment_only"
    operation = operations[0]
    if (operation[:4] != (prefix + "assessment", "assessment", "settled", "valid_json")
            or operation[4] is None or operation[4] > at):
        return None, "original_assessment_not_verified"
    if conn.execute(
        "SELECT 1 FROM coach_ai_operations WHERE operation_id LIKE %s LIMIT 1",
        (_paid_prefix(job.id, recovery=True) + "%",),
    ).fetchone():
        return None, "recovery_operation_already_attempted"
    return source, "eligible_completed_direct"


def recover_completed_direct(
    config, *, since: datetime, until: datetime, expected_chat_id: int,
    dry_run: bool = True, connection_factory=None, now: datetime | None = None,
) -> dict:
    """Preview/apply at most one exact request in an explicit one-hour window.

    Ambiguity blocks the operation. A factory must return a fresh connection;
    preview uses PostgreSQL READ ONLY and apply shares the live writer locks.
    All returned fields are allowlisted counts/reasons, never request contents.
    """
    since, until = _utc(since, "since"), _utc(until, "until")
    at = _utc(now if now is not None else datetime.now(timezone.utc), "now")
    if since >= until or until-since > timedelta(hours=1):
        raise CoachRecoveryError("invalid_recovery_window")
    if type(dry_run) is not bool or type(expected_chat_id) is not int or not -(2**63-1) <= expected_chat_id < 0:
        raise CoachRecoveryError("invalid_recovery_parameters")
    if not all((config.enabled, config.ai_enabled, config.database_url,
                config.bot_user_id, config.scope_configured)):
        raise CoachRecoveryError("recovery_runtime_not_enabled")
    factory = connection_factory or (lambda: connect(
        config.database_url, autocommit=True, connect_timeout=5,
        options="-c lock_timeout=5000 -c statement_timeout=15000",
    ))
    with factory() as conn:
        with conn.transaction():
            if dry_run:
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            CoachScopeRepository(conn, bot_user_id=config.bot_user_id).assert_identity()
            candidates = conn.execute(
                "SELECT " + _JOB_COLUMNS + " FROM coach_jobs WHERE chat_id=%s AND mode='direct' "
                "AND occurred_at>=%s AND occurred_at<%s AND (status='completed' OR recovery_attempt=1) "
                "ORDER BY occurred_at,id LIMIT 2", (expected_chat_id, since, until),
            ).fetchall()
            if len(candidates) != 1:
                return _summary(dry_run=dry_run, seen=len(candidates),
                                reason="no_completed_direct" if not candidates else "ambiguous_direct_requests")
            job = _Job(*candidates[0])
            if job.recovery_attempt:
                return _summary(dry_run=dry_run, seen=1, reason="already_recovered", already=True)
            if dry_run:
                source, reason = _source_if_eligible(conn, config, job, at)
                return _summary(dry_run=True, seen=1, reason=reason, eligible=source is not None)
            # author_lock has an insert-only compatibility path for ordinary
            # intake. Recovery must never discover or reactivate any group.
            membership = conn.execute(
                "SELECT active FROM coach_group_memberships WHERE chat_id=%s FOR SHARE", (job.chat_id,),
            ).fetchone()
            if not membership or not membership[0]:
                return _summary(dry_run=False, seen=1, reason="group_inactive_or_unknown")
            repo = CoachRepository(conn, chat_id=job.chat_id, message_thread_id=job.message_thread_id,
                                   bot_user_id=config.bot_user_id,
                                   transient_ttl_seconds=config.transient_ttl_seconds,
                                   allow_group_messages=config.group_mode == "joined_groups")
            try:
                with repo.author_lock(job.author_user_id):
                    # The lock order matches intake, revocation, lifecycle and
                    # final delivery. A rival recovery can only become a no-op.
                    row = conn.execute("SELECT " + _JOB_COLUMNS + " FROM coach_jobs WHERE id=%s FOR UPDATE",
                                       (job.id,)).fetchone()
                    if row is None:
                        return _summary(dry_run=False, seen=1, reason="job_unavailable")
                    job = _Job(*row)
                    if job.recovery_attempt:
                        return _summary(dry_run=False, seen=1, reason="already_recovered", already=True)
                    source, reason = _source_if_eligible(conn, config, job, at)
                    if source is None:
                        return _summary(dry_run=False, seen=1, reason=reason)
                    changed = conn.execute(
                        "UPDATE coach_jobs SET status='pending',current_text=%s,source_checksum=%s,source_id=%s,"
                        "lease_token=NULL,lease_until=NULL,recovery_attempt=1,recovery_requested_at=%s,"
                        "reason='operator_recovery' WHERE id=%s AND status='completed' AND recovery_attempt=0 "
                        "RETURNING id", (source.available_text, source.checksum, source.source_id, at, job.id),
                    ).fetchone()
                    return _summary(dry_run=False, seen=1, reason="operator_recovery_rearmed" if changed else
                                    "job_changed", eligible=bool(changed), rearmed=bool(changed))
            except ArchivePermissionError:
                return _summary(dry_run=False, seen=1, reason="group_or_permissions_changed")
