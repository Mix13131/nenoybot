"""Explicit operator backfill, sharing the live archive's locks and tombstones.

The caller verifies this dedicated bot and the exported group through Telegram.
No webhook, LLM job or outbound message is synthesized. The caller owns commit.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app_v2.adapters.telegram_history import HistoricalMessage, TelegramHistoryExport
from app_v2.repositories.coach_scope_repo import CoachScopeRepository
from app_v2.repositories.source_archive_repo import ArchiveIdentityConflict, ArchivePermissionError


_RECEIVED_UPDATES = datetime(1970, 1, 1, tzinfo=timezone.utc)


class SourceHistoryImportRepository:
    def __init__(self, conn, *, bot_user_id: int):
        self.conn = conn
        self.registry = CoachScopeRepository(conn, bot_user_id=bot_user_id)

    def import_export(self, export: TelegramHistoryExport, *, now: datetime | None = None) -> dict[str, int]:
        """Import one checked snapshot atomically; never revive an existing opt-out.

        New group defaults are recorded now with their true system basis. The
        epoch cutoff matches joined-group intake when no membership event was
        received; it is not a fabricated personal agreement in the past.
        """
        at = now or datetime.now(timezone.utc)
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("An aware import timestamp is required")
        at = at.astimezone(timezone.utc)
        if any(m.chat_id != export.chat_id or m.revision_at > at for m in export.messages):
            raise ArchiveIdentityConflict("Invalid export scope or future source revision")
        counts = {
            "input_events": export.input_messages, "candidates": len(export.messages),
            "topics": len({m.message_thread_id for m in export.messages}),
            "authors": len({m.author_user_id for m in export.messages}),
            "archived": 0, "duplicates": export.duplicate_messages, "deleted": 0,
            "permission_skipped": 0,
            "external_content": sum(m.contains_external_content for m in export.messages),
            "partial_bodies": sum(m.body_coverage != "full" for m in export.messages),
            "analysis_jobs_created": 0, "telegram_messages_sent": 0,
        }
        counts.update({"skipped_" + k: v for k, v in export.skipped_counts.items()})
        with self.conn.transaction():
            self.registry.assert_identity()
            # Serializes imports with all live intake, leave/rejoin and delivery
            # for this one group. Existing removal state always wins.
            self.conn.execute(
                "INSERT INTO coach_group_memberships(chat_id,active,active_since,changed_at) "
                "VALUES(%s,TRUE,%s,%s) ON CONFLICT DO NOTHING",
                (export.chat_id, _RECEIVED_UPDATES, at),
            )
            membership = self.conn.execute(
                "SELECT active,active_since FROM coach_group_memberships WHERE chat_id=%s FOR UPDATE",
                (export.chat_id,),
            ).fetchone()
            if not membership[0]:
                raise ArchivePermissionError("History import cannot reactivate a removed group")
            batch_id = str(uuid4())
            self.conn.execute(
                "INSERT INTO source_archive_imports(id,chat_id,file_sha256,source_format,parser_version,"
                "imported_at,input_events) VALUES(%s,%s,%s,'telegram_desktop_json',1,%s,%s) "
                "ON CONFLICT(chat_id,file_sha256) DO NOTHING",
                (batch_id, export.chat_id, export.export_sha256, at, export.input_messages),
            )
            batch_id = self.conn.execute(
                "SELECT id FROM source_archive_imports WHERE chat_id=%s AND file_sha256=%s",
                (export.chat_id, export.export_sha256),
            ).fetchone()[0]
            for topic in sorted({m.message_thread_id for m in export.messages}):
                self.conn.execute(
                    "INSERT INTO source_archive_scopes(chat_id,message_thread_id,topic_scope,enabled,activated_at) "
                    "VALUES(%s,%s,%s,TRUE,%s) ON CONFLICT DO NOTHING",
                    (export.chat_id, topic, "group" if topic == 1 else "forum_topic", membership[1]),
                )
            # Stable scope/author/parent order also protects first-time grants.
            for message in sorted(export.messages, key=lambda m: (
                m.message_thread_id, m.author_user_id, m.telegram_message_id,
            )):
                status = self._capture(message, batch_id=batch_id, at=at)
                counts[status] += 1
            counts["available_sources"] = self.conn.execute(
                "SELECT count(*) FROM source_archive_messages m JOIN source_archive_revisions r "
                "ON r.source_id=m.latest_source_id JOIN source_archive_consents c "
                "ON c.chat_id=m.chat_id AND c.message_thread_id=m.message_thread_id AND c.author_user_id=m.author_user_id "
                "JOIN source_archive_scopes s ON s.chat_id=m.chat_id AND s.message_thread_id=m.message_thread_id "
                "WHERE m.chat_id=%s AND m.message_thread_id=ANY(%s) AND NOT m.is_deleted "
                "AND r.source_status='active' AND r.available_text IS NOT NULL AND s.enabled AND c.retain_source",
                (export.chat_id, sorted({m.message_thread_id for m in export.messages})),
            ).fetchone()[0]
        return counts

    def _capture(self, message: HistoricalMessage, *, batch_id: str, at: datetime) -> str:
        key = (message.chat_id, message.message_thread_id, message.author_user_id)
        scope = self.conn.execute(
            "SELECT enabled,activated_at FROM source_archive_scopes "
            "WHERE chat_id=%s AND message_thread_id=%s FOR SHARE", key[:2],
        ).fetchone()
        if not scope or not scope[0]:
            return "permission_skipped"
        created = self.conn.execute(
            "INSERT INTO source_archive_consents(chat_id,message_thread_id,author_user_id,version,"
            "retain_source,historical_inference,auto_report_analysis,unsolicited_group_reply,"
            "direct_request_group_reply,retain_since,updated_at,actor_user_id,grant_basis) "
            "VALUES(%s,%s,%s,1,TRUE,TRUE,TRUE,TRUE,TRUE,%s,%s,NULL,'group_default') "
            "ON CONFLICT DO NOTHING RETURNING version", (*key, scope[1], at),
        ).fetchone()
        if created:
            self.conn.execute(
                "INSERT INTO source_archive_consent_versions(chat_id,message_thread_id,author_user_id,version,"
                "retain_source,historical_inference,auto_report_analysis,unsolicited_group_reply,"
                "direct_request_group_reply,retain_since,recorded_at,grant_basis,actor_user_id) "
                "VALUES(%s,%s,%s,1,TRUE,TRUE,TRUE,TRUE,TRUE,%s,%s,'group_default',NULL)",
                (*key, scope[1], at),
            )
            self.conn.execute(
                "INSERT INTO coach_participant_state(chat_id,message_thread_id,author_user_id,track_status) "
                "VALUES(%s,%s,%s,'active') ON CONFLICT DO NOTHING", key,
            )
        grant = self.conn.execute(
            "SELECT version,retain_source,retain_since,grant_basis FROM source_archive_consents "
            "WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s FOR UPDATE", key,
        ).fetchone()
        # A personal grant (including full forget's new boundary) must not be
        # overwritten or bypassed by a later administrator-supplied old export.
        if not grant[1] or (grant[3] == "self" and message.occurred_at < grant[2]):
            return "permission_skipped"
        if self.conn.execute(
            "SELECT 1 FROM source_archive_deletions WHERE chat_id=%s AND message_thread_id=%s "
            "AND author_user_id=%s AND telegram_message_id=%s", (*key, message.telegram_message_id),
        ).fetchone():
            return "deleted"
        self.conn.execute(
            "INSERT INTO source_archive_messages(chat_id,message_thread_id,author_user_id,telegram_message_id,occurred_at) "
            "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(chat_id,telegram_message_id) DO NOTHING",
            (*key, message.telegram_message_id, message.occurred_at),
        )
        parent = self.conn.execute(
            "SELECT message_thread_id,author_user_id,occurred_at,is_deleted,latest_source_id "
            "FROM source_archive_messages WHERE chat_id=%s AND telegram_message_id=%s FOR UPDATE",
            (message.chat_id, message.telegram_message_id),
        ).fetchone()
        if parent[:3] != (message.message_thread_id, message.author_user_id, message.occurred_at):
            raise ArchiveIdentityConflict("Imported message identity conflicts with the archive")
        if parent[3]:
            return "deleted"
        previous = self.conn.execute(
            "SELECT checksum,source_status FROM source_archive_revisions WHERE chat_id=%s "
            "AND telegram_message_id=%s AND revision_at=%s AND source_origin='historical_import'",
            (message.chat_id, message.telegram_message_id, message.revision_at),
        ).fetchone()
        if previous:
            if previous[1] == "deleted":
                return "deleted"
            if previous[0] != message.checksum:
                raise ArchiveIdentityConflict("Conflicting imported snapshots have the same revision timestamp")
            return "duplicates"
        revision = self.conn.execute(
            "SELECT COALESCE(MAX(message_revision),0)+1 FROM source_archive_revisions "
            "WHERE chat_id=%s AND telegram_message_id=%s", (message.chat_id, message.telegram_message_id),
        ).fetchone()[0]
        source_id = f"tghist:{message.chat_id}:{message.message_thread_id}:{message.telegram_message_id}:{batch_id}"
        self.conn.execute(
            "INSERT INTO source_archive_revisions(source_id,chat_id,message_thread_id,author_user_id,"
            "telegram_message_id,telegram_update_id,message_revision,occurred_at,revision_at,captured_at,is_edited,"
            "reply_to_message_id,content_kind,available_text,body_coverage,media_reference,contains_external_content,"
            "consent_version,checksum,source_origin,import_batch_id) "
            "VALUES(%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s,%s,%s,%s,%s,%s,'{}'::jsonb,%s,%s,%s,'historical_import',%s)",
            (source_id, *key, message.telegram_message_id, revision, message.occurred_at,
             message.revision_at, at, message.is_edited, message.reply_to_message_id, message.content_kind,
             message.available_text, message.body_coverage, message.contains_external_content,
             grant[0], message.checksum, batch_id),
        )
        # Equal timestamps prefer a live Telegram update. Updating the parent
        # invokes the existing derived-memory/outbox invalidation triggers.
        self.conn.execute(
            "UPDATE source_archive_messages SET latest_source_id=%s WHERE chat_id=%s AND telegram_message_id=%s "
            "AND (latest_source_id IS NULL OR EXISTS(SELECT 1 FROM source_archive_revisions r "
            "WHERE r.source_id=latest_source_id AND r.revision_at<%s))",
            (source_id, message.chat_id, message.telegram_message_id, message.revision_at),
        )
        return "archived"
