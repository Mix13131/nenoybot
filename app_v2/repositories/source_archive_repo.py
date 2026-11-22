from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from app_v2.adapters.telegram_archive import archive_message_from_update
from app_v2.adapters.telegram_webhook import NormalizedTelegramUpdate


class ArchivePermissionError(PermissionError):
    pass


class ArchiveIdentityConflict(ValueError):
    pass


@dataclass(frozen=True)
class ArchiveCapture:
    status: str
    source_id: str | None = None
    checksum: str | None = None
    message_revision: int | None = None


@dataclass(frozen=True)
class ArchiveSource:
    source_id: str
    chat_id: int
    message_thread_id: int
    author_user_id: int
    telegram_message_id: int
    telegram_update_id: int
    message_revision: int
    occurred_at: datetime
    revision_at: datetime
    captured_at: datetime
    is_edited: bool
    reply_to_message_id: int | None
    content_kind: str
    available_text: str | None
    body_coverage: str
    media_reference: dict[str, Any]
    contains_external_content: bool
    consent_version: int
    checksum: str
    access_version: int


@dataclass(frozen=True)
class DeletionReceipt:
    messages: int
    revisions: int
    scope: str = 'reflection_source_archive_only'
    backups_erased: bool = False
    delivered_messages_erased: bool = False
    legacy_stores_erased: bool = False


_SOURCE_COLUMNS = '''r.source_id, r.chat_id, r.message_thread_id, r.author_user_id,
    r.telegram_message_id, r.telegram_update_id, r.message_revision,
    r.occurred_at, r.revision_at, r.captured_at, r.is_edited,
    r.reply_to_message_id, r.content_kind, r.available_text, r.body_coverage,
    r.media_reference, r.contains_external_content, r.consent_version,
    r.checksum, c.version'''


def _scope(chat_id: int, message_thread_id: int, author_user_id: int | None = None) -> None:
    if type(chat_id) is not int or chat_id >= 0:
        raise ValueError('A Telegram group ID is required')
    if type(message_thread_id) is not int or message_thread_id <= 0:
        raise ValueError('An explicit forum topic ID is required')
    if author_user_id is not None and (type(author_user_id) is not int or author_user_id <= 0):
        raise ValueError('An identified human author is required')


def _time(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Archive timestamps must be timezone-aware')
    return value.astimezone(timezone.utc)


def _self(actor_user_id: int, author_user_id: int) -> None:
    if type(actor_user_id) is not int or actor_user_id != author_user_id:
        raise ArchivePermissionError('A participant may authorize or export only their own sources')


def _eligible_time(value: datetime) -> datetime:
    # Telegram message dates have second resolution. Round UP so events sent
    # before consent in the same second can never be treated as authorized.
    # This explicit boundary is also returned by coverage().
    return value + timedelta(microseconds=1_000_000 - value.microsecond) if value.microsecond else value


class SourceArchiveRepository:
    """Deterministic Stage A storage; no LLM, vector service, send or implicit commit.

    All IDs are external Telegram IDs, never legacy users/chats primary keys.
    Mutations use transactions (savepoints inside the ingest transaction). Callers
    own the outer commit. Authentication of actor_user_id belongs to the trusted
    Telegram/operator boundary; this internal API is not a public endpoint.
    """

    def __init__(self, conn) -> None:
        self.conn = conn

    def configure_scope(
        self, chat_id: int, message_thread_id: int, *, enabled: bool,
        activated_at: datetime | None = None,
    ) -> None:
        """Operator-only configuration. Creates no participant grants."""
        _scope(chat_id, message_thread_id)
        if type(enabled) is not bool:
            raise ValueError('enabled must be a boolean')
        with self.conn.transaction():
            self.conn.execute(
                '''INSERT INTO source_archive_scopes(chat_id, message_thread_id, enabled, activated_at)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (chat_id, message_thread_id) DO UPDATE
                   SET enabled = EXCLUDED.enabled,
                       activated_at = CASE
                           WHEN NOT source_archive_scopes.enabled AND EXCLUDED.enabled
                           THEN GREATEST(source_archive_scopes.activated_at, EXCLUDED.activated_at)
                           ELSE source_archive_scopes.activated_at END''',
                (chat_id, message_thread_id, enabled, _eligible_time(_time(activated_at))),
            )

    def set_permissions(
        self, *, chat_id: int, message_thread_id: int, author_user_id: int,
        actor_user_id: int, retain_source: bool = False,
        historical_inference: bool = False, auto_report_analysis: bool = False,
        unsolicited_group_reply: bool = False, direct_request_group_reply: bool = False,
        now: datetime | None = None,
    ) -> int:
        """Replace five independent explicit grants; revoke purges live source bodies.

        `now` is an injectable clock for synthetic tests, not a history-import API.
        No grant or version is inferred from legacy profiles or administrator role.
        """
        _scope(chat_id, message_thread_id, author_user_id)
        _self(actor_user_id, author_user_id)
        values = (retain_source, historical_inference, auto_report_analysis,
                  unsolicited_group_reply, direct_request_group_reply)
        if any(type(value) is not bool for value in values):
            raise ValueError('All permissions must be explicit booleans')
        at = _time(now)
        key = (chat_id, message_thread_id, author_user_id)
        with self.conn.transaction():
            scope = self.conn.execute(
                '''SELECT enabled FROM source_archive_scopes
                   WHERE chat_id=%s AND message_thread_id=%s FOR SHARE''', key[:2],
            ).fetchone()
            if scope is None:
                raise ArchivePermissionError('The exact forum topic is not configured')
            # Seed a deny row, then lock it. The insert's uniqueness check also
            # serializes concurrent first-time consent updates.
            self.conn.execute(
                '''INSERT INTO source_archive_consents(
                       chat_id, message_thread_id, author_user_id, version, updated_at, actor_user_id)
                   VALUES (%s, %s, %s, 1, %s, %s)
                   ON CONFLICT (chat_id, message_thread_id, author_user_id) DO NOTHING''',
                (*key, at, actor_user_id),
            )
            current = self.conn.execute(
                '''SELECT version, retain_source, retain_since, updated_at
                   FROM source_archive_consents
                   WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s FOR UPDATE''', key,
            ).fetchone()
            if at < current[3]:
                raise ValueError('Consent cannot be backdated')
            exists = self.conn.execute(
                '''SELECT 1 FROM source_archive_consent_versions
                   WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s AND version=%s''',
                (*key, current[0]),
            ).fetchone()
            version = current[0] + 1 if exists else current[0]
            retain_since = (current[2] if current[1] else _eligible_time(at)) if retain_source else None
            self.conn.execute(
                '''UPDATE source_archive_consents SET version=%s, retain_source=%s,
                       historical_inference=%s, auto_report_analysis=%s, unsolicited_group_reply=%s,
                       direct_request_group_reply=%s, retain_since=%s, updated_at=%s, actor_user_id=%s
                   WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s''',
                (version, *values, retain_since, at, actor_user_id, *key),
            )
            self.conn.execute(
                '''INSERT INTO source_archive_consent_versions(
                       chat_id, message_thread_id, author_user_id, version, retain_source,
                       historical_inference, auto_report_analysis, unsolicited_group_reply,
                       direct_request_group_reply, retain_since, recorded_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                (*key, version, *values, retain_since, at),
            )
            if not retain_source:
                self._purge(key)
            return int(version)

    def capture(self, normalized: NormalizedTelegramUpdate) -> ArchiveCapture:
        message = archive_message_from_update(normalized)
        if message is None:
            return ArchiveCapture('ignored')
        key = (message.chat_id, message.message_thread_id, message.author_user_id)
        with self.conn.transaction():
            # Consent lock is shared with revoke/forget. No insert can race past
            # a completed revocation. Scope disabling waits for this transaction.
            grant = self.conn.execute(
                '''SELECT c.version, c.retain_since, s.activated_at
                   FROM source_archive_consents c JOIN source_archive_scopes s
                     USING (chat_id, message_thread_id)
                   WHERE c.chat_id=%s AND c.message_thread_id=%s AND c.author_user_id=%s
                     AND c.retain_source AND s.enabled
                   FOR SHARE OF s FOR UPDATE OF c''', key,
            ).fetchone()
            if grant is None or message.occurred_at < max(grant[1], grant[2]):
                return ArchiveCapture('ignored')
            existing = self.conn.execute(
                '''SELECT source_id, checksum, message_revision, source_status
                   FROM source_archive_revisions WHERE telegram_update_id=%s''',
                (message.telegram_update_id,),
            ).fetchone()
            if existing:
                if existing[0] != message.source_id or (
                    existing[3] == 'active' and existing[1] != message.checksum
                ):
                    raise ArchiveIdentityConflict('An update ID was reused with conflicting source data')
                return ArchiveCapture('duplicate' if existing[3] == 'active' else 'deleted',
                                      existing[0], existing[1], existing[2])

            self.conn.execute(
                '''INSERT INTO source_archive_messages(
                       chat_id, message_thread_id, author_user_id, telegram_message_id, occurred_at)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (chat_id, telegram_message_id) DO NOTHING''',
                (*key, message.telegram_message_id, message.occurred_at),
            )
            parent = self.conn.execute(
                '''SELECT message_thread_id, author_user_id, occurred_at, is_deleted, latest_source_id
                   FROM source_archive_messages WHERE chat_id=%s AND telegram_message_id=%s FOR UPDATE''',
                (message.chat_id, message.telegram_message_id),
            ).fetchone()
            if parent[:3] != (message.message_thread_id, message.author_user_id, message.occurred_at):
                raise ArchiveIdentityConflict('Message topic, author and creation time are immutable')
            if parent[3]:
                return ArchiveCapture('deleted')
            revision = self.conn.execute(
                '''SELECT COALESCE(MAX(message_revision), 0) + 1 FROM source_archive_revisions
                   WHERE chat_id=%s AND telegram_message_id=%s''',
                (message.chat_id, message.telegram_message_id),
            ).fetchone()[0]
            self.conn.execute(
                '''INSERT INTO source_archive_revisions(
                       source_id, chat_id, message_thread_id, author_user_id, telegram_message_id,
                       telegram_update_id, message_revision, occurred_at, revision_at, is_edited,
                       reply_to_message_id, content_kind, available_text, body_coverage,
                       media_reference, contains_external_content, consent_version, checksum)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)''',
                (message.source_id, *key, message.telegram_message_id, message.telegram_update_id,
                 revision, message.occurred_at, message.revision_at, message.is_edited,
                 message.reply_to_message_id, message.content_kind, message.available_text,
                 message.body_coverage, json.dumps(message.media_reference, ensure_ascii=False),
                 message.contains_external_content, grant[0], message.checksum),
            )
            # Arrival order is not edit order. A late original or earlier edit
            # appends to lineage but cannot replace a later Telegram revision.
            self.conn.execute(
                '''UPDATE source_archive_messages SET latest_source_id=%s
                   WHERE chat_id=%s AND telegram_message_id=%s
                     AND (latest_source_id IS NULL OR EXISTS (
                         SELECT 1 FROM source_archive_revisions r
                         WHERE r.source_id=latest_source_id
                           AND (r.revision_at, r.telegram_update_id) < (%s, %s)))''',
                (message.source_id, message.chat_id, message.telegram_message_id,
                 message.revision_at, message.telegram_update_id),
            )
            return ArchiveCapture('archived', message.source_id, message.checksum, revision)

    def _purge(self, key: tuple[int, int, int], telegram_message_id: int | None = None) -> DeletionReceipt:
        message_filter = '' if telegram_message_id is None else ' AND telegram_message_id=%s'
        params = key if telegram_message_id is None else (*key, telegram_message_id)
        revisions = self.conn.execute(
            '''UPDATE source_archive_revisions
               SET available_text=NULL, media_reference='{}'::jsonb, checksum=NULL,
                   source_status='deleted', deleted_at=CURRENT_TIMESTAMP
               WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s
                 AND source_status='active' ''' + message_filter, params,
        ).rowcount
        messages = self.conn.execute(
            '''UPDATE source_archive_messages SET is_deleted=TRUE, latest_source_id=NULL
               WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s
                 AND NOT is_deleted ''' + message_filter, params,
        ).rowcount
        return DeletionReceipt(messages, revisions)

    def forget_sources(
        self, *, chat_id: int, message_thread_id: int, author_user_id: int,
        actor_user_id: int, telegram_message_id: int | None = None,
        now: datetime | None = None,
    ) -> DeletionReceipt:
        _scope(chat_id, message_thread_id, author_user_id)
        _self(actor_user_id, author_user_id)
        if telegram_message_id is not None and (
            type(telegram_message_id) is not int or telegram_message_id <= 0
        ):
            raise ValueError('A positive message ID is required')
        key = (chat_id, message_thread_id, author_user_id)
        with self.conn.transaction():
            current = self.conn.execute(
                '''SELECT version, retain_source, updated_at FROM source_archive_consents
                   WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s FOR UPDATE''', key,
            ).fetchone()
            receipt = self._purge(key, telegram_message_id)
            if telegram_message_id is None and current and current[1]:
                at = _time(now)
                if at < current[2]:
                    raise ValueError('Forget requests cannot be backdated')
                # Forgetting the whole scope establishes a new start boundary.
                # An unseen delayed old message cannot refill the erased period.
                # Future messages remain enabled under the same five grants.
                self.conn.execute(
                    '''UPDATE source_archive_consents
                       SET version=version+1, retain_since=%s, updated_at=%s
                       WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s''',
                    (_eligible_time(at), at, *key),
                )
                self.conn.execute(
                    '''INSERT INTO source_archive_consent_versions(
                           chat_id, message_thread_id, author_user_id, version, retain_source,
                           historical_inference, auto_report_analysis, unsolicited_group_reply,
                           direct_request_group_reply, retain_since, recorded_at)
                       SELECT chat_id, message_thread_id, author_user_id, version, retain_source,
                           historical_inference, auto_report_analysis, unsolicited_group_reply,
                           direct_request_group_reply, retain_since, updated_at
                       FROM source_archive_consents
                       WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s''', key,
                )
            return receipt

    @staticmethod
    def _purpose(purpose: str, actor_user_id: int, author_user_id: int) -> str:
        # Stage A has no group reply path. On-demand permission is deliberately
        # not treated as permission to reveal source exports to other members.
        _self(actor_user_id, author_user_id)
        if purpose == 'owner_export':
            return ''
        if purpose == 'historical_inference':
            return ' AND c.historical_inference AND NOT r.contains_external_content'
        raise ArchivePermissionError('Unsupported archive read purpose')

    def query_sources(
        self, *, chat_id: int, message_thread_id: int, author_user_id: int,
        actor_user_id: int, since: datetime, until: datetime,
        purpose: str = 'owner_export', limit: int = 100,
        after: tuple[datetime, str] | None = None, history: bool = False,
    ) -> list[ArchiveSource]:
        _scope(chat_id, message_thread_id, author_user_id)
        extra = self._purpose(purpose, actor_user_id, author_user_id)
        since, until = _time(since), _time(until)
        if since >= until or type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('Use a nonempty date window and a page size from 1 to 200')
        if history and purpose != 'owner_export':
            raise ArchivePermissionError('Superseded revisions are available only for an author export')
        params: tuple = (chat_id, message_thread_id, author_user_id, since, until)
        order = 'r.occurred_at, r.source_id' if history else 'r.occurred_at, r.telegram_message_id'
        if not history:
            extra += ' AND m.latest_source_id=r.source_id'
        if after is not None:
            if history:
                extra += ' AND (r.occurred_at, r.source_id) > (%s, %s)'
                params += (_time(after[0]), after[1])
            else:
                # Source IDs identify revisions, but a latest-message scan must
                # not repeat a previously exported message merely after an edit.
                parts = after[1].split(':')
                if len(parts) != 5 or parts[:3] != ['tgsrc', str(chat_id), str(message_thread_id)]:
                    raise ValueError('Cursor does not belong to this source scope')
                try:
                    message_id = int(parts[3])
                except ValueError as exc:
                    raise ValueError('Invalid source cursor') from exc
                if message_id <= 0:
                    raise ValueError('Invalid source cursor')
                extra += ' AND (r.occurred_at, r.telegram_message_id) > (%s, %s)'
                params += (_time(after[0]), message_id)
        rows = self.conn.execute(
            'SELECT ' + _SOURCE_COLUMNS + ''' FROM source_archive_revisions r
               JOIN source_archive_messages m
                 ON m.chat_id=r.chat_id AND m.telegram_message_id=r.telegram_message_id
               JOIN source_archive_consents c
                 ON c.chat_id=r.chat_id AND c.message_thread_id=r.message_thread_id
                    AND c.author_user_id=r.author_user_id
               JOIN source_archive_scopes s
                 ON s.chat_id=r.chat_id AND s.message_thread_id=r.message_thread_id
               WHERE r.chat_id=%s AND r.message_thread_id=%s AND r.author_user_id=%s
                 AND r.occurred_at >= %s AND r.occurred_at < %s
                 AND r.source_status='active' AND NOT m.is_deleted
                 AND s.enabled AND c.retain_source''' + extra +
            ' ORDER BY ' + order + ' LIMIT %s', (*params, limit),
        ).fetchall()
        return [ArchiveSource(*row) for row in rows]

    def reauthorize(
        self, sources: Sequence[ArchiveSource], *, chat_id: int, message_thread_id: int,
        author_user_id: int, actor_user_id: int, purpose: str = 'historical_inference',
    ) -> bool:
        """Invalidate stale source/consent snapshots. Not a Telegram send operation.

        Stage B must call this at its final dispatch boundary and coordinate
        send/revoke there. An earlier True is not a transferable send permission.
        """
        _scope(chat_id, message_thread_id, author_user_id)
        extra = self._purpose(purpose, actor_user_id, author_user_id)
        if not sources or len(sources) > 200:
            return False
        if any((s.chat_id, s.message_thread_id, s.author_user_id) !=
               (chat_id, message_thread_id, author_user_id) for s in sources):
            return False
        rows = self.conn.execute(
            '''SELECT r.source_id, r.checksum, c.version FROM source_archive_revisions r
               JOIN source_archive_messages m
                 ON m.chat_id=r.chat_id AND m.telegram_message_id=r.telegram_message_id
               JOIN source_archive_consents c
                 ON c.chat_id=r.chat_id AND c.message_thread_id=r.message_thread_id
                    AND c.author_user_id=r.author_user_id
               JOIN source_archive_scopes s
                 ON s.chat_id=r.chat_id AND s.message_thread_id=r.message_thread_id
               WHERE r.chat_id=%s AND r.message_thread_id=%s AND r.author_user_id=%s
                 AND r.source_id=ANY(%s) AND r.source_status='active'
                 AND NOT m.is_deleted AND m.latest_source_id=r.source_id
                 AND s.enabled AND c.retain_source''' + extra,
            (chat_id, message_thread_id, author_user_id, [s.source_id for s in sources]),
        ).fetchall()
        current = {row[0]: (row[1], row[2]) for row in rows}
        return all(current.get(s.source_id) == (s.checksum, s.access_version) for s in sources)

    def coverage(
        self, *, chat_id: int, message_thread_id: int, author_user_id: int,
        actor_user_id: int, since: datetime, until: datetime,
    ) -> dict[str, Any]:
        """Truthful received-source coverage, including explicit unknowns."""
        _scope(chat_id, message_thread_id, author_user_id)
        _self(actor_user_id, author_user_id)
        since, until = _time(since), _time(until)
        if since >= until:
            raise ValueError('Use a nonempty date window')
        key = (chat_id, message_thread_id, author_user_id)
        grant = self.conn.execute(
            '''SELECT s.enabled, s.activated_at, c.retain_source, c.retain_since
               FROM source_archive_scopes s JOIN source_archive_consents c
                 USING (chat_id, message_thread_id)
               WHERE c.chat_id=%s AND c.message_thread_id=%s AND c.author_user_id=%s''', key,
        ).fetchone()
        earliest = max(grant[1], grant[3]) if grant and grant[3] else None
        row = self.conn.execute(
            '''SELECT COUNT(*), COUNT(*) FILTER (WHERE source_status='deleted'),
                      COUNT(DISTINCT telegram_message_id) FILTER (WHERE source_status='active'),
                      COUNT(*) FILTER (WHERE body_coverage <> 'full' AND source_status='active'),
                      MIN(occurred_at) FILTER (WHERE source_status='active'),
                      MAX(occurred_at) FILTER (WHERE source_status='active')
               FROM source_archive_revisions
               WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s
                 AND occurred_at >= %s AND occurred_at < %s''', (*key, since, until),
        ).fetchone()
        return {
            'basis': 'received_authorized_events_only',
            'enabled': bool(grant and grant[0] and grant[2]),
            'eligible_since': earliest,
            'has_pre_activation_gap': earliest is None or since < earliest,
            'revisions': row[0], 'deleted_revisions': row[1], 'messages': row[2],
            'partial_or_unavailable_revisions': row[3],
            'first_message_at': row[4], 'last_message_at': row[5],
            'complete_telegram_history': False,
            'missing_delivery_detection': 'unavailable',
            'automatic_telegram_deletion_detection': False,
            'historical_import_performed': False,
            'limitations': (
                'Only received events are known; delivery gaps and disabled periods may be unknown.',
                'No pre-consent history or media bytes were imported.',
                'Deletion covers live Reflection sources, not legacy stores, sent messages or backups.',
            ),
        }
