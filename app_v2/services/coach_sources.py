"""Bounded source selection across an entire authorized date window.

This selector never reads generic messages, event payloads or Personal memory.
It returns a disclosed selection, not a claim to have read every archived body.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from app_v2.repositories.source_archive_repo import ArchiveSource


@dataclass(frozen=True)
class CoachSourceSelection:
    sources: tuple[ArchiveSource, ...] = ()
    total_sources: int = 0
    eligible_sources: int = 0
    matched_sources: int = 0
    partial_sources: int = 0
    since: datetime | None = None
    until: datetime | None = None
    strategy: str = 'none'

    def disclosure(self) -> dict:
        return {
            'total_received_sources_in_window': self.total_sources,
            'eligible_text_sources_in_window': self.eligible_sources,
            'matched_sources': self.matched_sources,
            'selected_sources': len(self.sources),
            'omitted_eligible_sources': max(0, self.eligible_sources - len(self.sources)),
            'partial_media_sources': self.partial_sources,
            'since': self.since.isoformat() if self.since else None,
            'until': self.until.isoformat() if self.until else None,
            'selection_strategy': self.strategy,
            'complete_telegram_history': False,
            'unknown_delivery_gaps': True,
            'media_bytes_analyzed': False,
            'selected_imported_snapshots': sum(s.source_origin == 'historical_import' for s in self.sources),
        }


_NAMES = (
    'source_id', 'chat_id', 'message_thread_id', 'author_user_id',
    'telegram_message_id', 'telegram_update_id', 'message_revision',
    'occurred_at', 'revision_at', 'captured_at', 'is_edited', 'reply_to_message_id',
    'content_kind', 'available_text', 'body_coverage', 'media_reference',
    'contains_external_content', 'consent_version', 'checksum', 'access_version',
    'source_origin', 'import_batch_id',
)
_COLUMNS = ', '.join('c.version AS access_version' if name == 'access_version' else 'r.' + name for name in _NAMES)
_JOIN = '''FROM source_archive_revisions r
    JOIN source_archive_messages m ON m.chat_id=r.chat_id
      AND m.telegram_message_id=r.telegram_message_id AND m.latest_source_id=r.source_id
    JOIN source_archive_consents c ON c.chat_id=r.chat_id
      AND c.message_thread_id=r.message_thread_id AND c.author_user_id=r.author_user_id
    JOIN source_archive_scopes s ON s.chat_id=r.chat_id AND s.message_thread_id=r.message_thread_id
    WHERE r.chat_id=%s AND r.message_thread_id=%s AND r.author_user_id=%s
      AND r.source_status='active' AND NOT m.is_deleted AND s.enabled AND c.retain_source'''


class CoachSourceSelector:
    def __init__(self, archive) -> None:
        self.archive = archive

    def current(self, *, chat_id: int, message_thread_id: int, author_user_id: int,
                source_id: str) -> ArchiveSource | None:
        row = self.archive.conn.execute(
            'SELECT ' + _COLUMNS + ' ' + _JOIN + ' AND r.source_id=%s',
            (chat_id, message_thread_id, author_user_id, source_id),
        ).fetchone()
        return ArchiveSource(*row) if row else None

    def select(self, *, chat_id: int, message_thread_id: int, author_user_id: int,
               since: datetime, until: datetime, terms: Sequence[str] = (),
               limit: int = 24) -> CoachSourceSelection:
        if since.tzinfo is None or until.tzinfo is None or since >= until:
            raise ValueError('An explicit nonempty timezone-aware window is required')
        if type(limit) is not int or not 1 <= limit <= 40:
            raise ValueError('Source selection limit must be between 1 and 40')
        if any(type(value) is not int for value in (chat_id, message_thread_id, author_user_id)):
            raise ValueError('Source scope IDs must be integers')
        if chat_id >= 0 or message_thread_id <= 0 or author_user_id <= 0:
            raise ValueError('An exact group/topic/author scope is required')
        if len(terms) > 6 or any(not isinstance(t, str) or not 2 <= len(t) <= 80 for t in terms):
            raise ValueError('At most six bounded search terms are accepted')
        params = (chat_id, message_thread_id, author_user_id, since, until)
        base = _JOIN + ''' AND c.historical_inference
            AND r.occurred_at >= %s AND r.occurred_at < %s'''
        counts = self.archive.conn.execute(
            '''SELECT COUNT(*), COUNT(*) FILTER (
                   WHERE NOT r.contains_external_content AND r.available_text IS NOT NULL),
                   COUNT(*) FILTER (WHERE r.body_coverage <> 'full') ''' + base, params,
        ).fetchone()
        eligible = base + ' AND NOT r.contains_external_content AND r.available_text IS NOT NULL'
        if terms:
            predicate = ''' AND EXISTS (SELECT 1 FROM unnest(%s::text[]) term
                WHERE strpos(lower(r.available_text), lower(term)) > 0)'''
            matched = self.archive.conn.execute('SELECT COUNT(*) ' + eligible + predicate,
                                                (*params, list(terms))).fetchone()[0]
            rows = self.archive.conn.execute(
                'SELECT ' + _COLUMNS + ' ' + eligible + predicate +
                ' ORDER BY r.occurred_at DESC, r.telegram_message_id DESC LIMIT %s',
                (*params, list(terms), limit),
            ).fetchall()
            strategy = 'keyword_search_entire_authorized_window'
        else:
            matched = counts[1]
            # Select representatives across the whole requested period. A
            # newest-N slice must not masquerade as a longitudinal review.
            rows = self.archive.conn.execute(
                'WITH candidate AS (SELECT ' + _COLUMNS + ' ' + eligible + '''),
                ranked AS (SELECT *, ntile(%s) OVER (
                    ORDER BY occurred_at, telegram_message_id) AS time_bucket FROM candidate)
                SELECT DISTINCT ON (time_bucket) ''' + ', '.join(_NAMES) + ''' FROM ranked
                ORDER BY time_bucket, occurred_at, telegram_message_id''', (*params, limit),
            ).fetchall()
            strategy = 'representatives_across_entire_authorized_window'
        return CoachSourceSelection(
            tuple(ArchiveSource(*row) for row in rows), int(counts[0]), int(counts[1]),
            int(matched), int(counts[2]), since, until, strategy,
        )
