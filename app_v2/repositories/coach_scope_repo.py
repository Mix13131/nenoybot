"""Dedicated bot identity and durable membership of its independent groups.

Only an authenticated Telegram webhook may discover a joined group. Ordinary
messages never reactivate a removal tombstone, an operator-disabled topic, or
an individual's opt-out. Lock order is group, then topic, then author/output.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app_v2.domain.source_archive import ArchiveMessage


_MAX_ID = (1 << 63) - 1
_RECEIVED_UPDATES = datetime(1970, 1, 1, tzinfo=timezone.utc)


class CoachConfigurationError(RuntimeError):
    """Preserved repository identity-error contract, separate from env parsing."""


@dataclass(frozen=True)
class CoachMembership:
    chat_id: int
    update_id: int
    occurred_at: datetime
    active: bool
    was_active: bool


def _member_active(member: dict) -> bool | None:
    status = member.get("status")
    if status in {"member", "administrator", "creator"}:
        return True
    if status in {"left", "kicked"}:
        return False
    if status == "restricted" and type(member.get("is_member")) is bool:
        return member["is_member"]
    return None


def membership_from_update(update: dict, *, bot_user_id: int) -> CoachMembership | None:
    """Accept only this bot's group membership, never another user's change."""
    value = update.get("my_chat_member")
    if not isinstance(value, dict):
        return None
    chat, member = value.get("chat"), value.get("new_chat_member")
    if not isinstance(chat, dict) or not isinstance(member, dict):
        return None
    user = member.get("user")
    if (chat.get("type") not in {"group", "supergroup"}
            or type(chat.get("id")) is not int or not -_MAX_ID <= chat["id"] < 0
            or not isinstance(user, dict) or user.get("is_bot") is not True
            or type(user.get("id")) is not int or user["id"] != bot_user_id
            or type(update.get("update_id")) is not int or not 0 <= update["update_id"] <= _MAX_ID
            or type(value.get("date")) is not int or value["date"] <= 0):
        return None
    previous = value.get("old_chat_member")
    if not isinstance(previous, dict):
        return None
    old_user = previous.get("user")
    if (not isinstance(old_user, dict) or old_user.get("is_bot") is not True
            or type(old_user.get("id")) is not int or old_user["id"] != bot_user_id):
        return None
    active, was_active = _member_active(member), _member_active(previous)
    if active is None or was_active is None:
        return None
    try:
        at = datetime.fromtimestamp(value["date"], tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None
    return CoachMembership(chat["id"], update["update_id"], at, active, was_active)


def cancel_failed_bundle_tails(conn, *, bundle_id=None, scope=None):
    restriction, params = "", ()
    if bundle_id is not None:
        restriction = " AND pending.bundle_id=%s"
        params = (bundle_id,)
    elif scope is not None:
        restriction = " AND pending.chat_id=%s AND pending.message_thread_id=%s"
        params = tuple(scope)
    conn.execute(
        """UPDATE coaching_outputs pending SET status='cancelled',text=NULL,source_proofs='[]'::jsonb,
           request_checksum=NULL,source_id=NULL,lease_token=NULL,lease_until=NULL
           WHERE pending.status IN ('pending','sending') AND EXISTS (
             SELECT 1 FROM coaching_outputs failed WHERE failed.bundle_id=pending.bundle_id
               AND failed.part_index<pending.part_index AND failed.status IN ('cancelled','uncertain','expired'))"""
        + restriction, params,
    )


def _after_join(at: datetime, received_update_id: int, since: datetime, join_update_id: int | None) -> bool:
    if at != since or join_update_id is None:
        return at >= since
    return received_update_id > join_update_id


class CoachScopeRepository:
    def __init__(self, conn, *, bot_user_id: int):
        if type(bot_user_id) is not int or not 0 < bot_user_id <= _MAX_ID:
            raise ValueError("A dedicated bot identity is required")
        self.conn, self.bot_user_id = conn, bot_user_id

    def bind_identity(self):
        """Explicit preparation only; never silently bind a legacy database."""
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

    def list_scopes(self, *, include_disabled: bool = False) -> list[tuple[int, int]]:
        self.assert_identity()
        return self.conn.execute(
            "SELECT s.chat_id,s.message_thread_id FROM source_archive_scopes s "
            "LEFT JOIN coach_group_memberships g USING(chat_id) "
            "WHERE COALESCE(g.active,TRUE) "
            + ("" if include_disabled else "AND s.enabled ")
            + "ORDER BY s.chat_id,s.message_thread_id"
        ).fetchall()

    def apply_membership(self, event: CoachMembership) -> bool:
        """Serialize lifecycle events with intake and final delivery.

        Timestamp precedes update_id in ordering because Telegram may restart
        update IDs after a long idle interval. Duplicate/late joins are inert.
        """
        self.assert_identity()
        with self.conn.transaction():
            self.conn.execute(
                "INSERT INTO coach_group_memberships(chat_id,active,active_since,changed_at,membership_update_id) "
                "VALUES(%s,%s,%s,%s,NULL) ON CONFLICT DO NOTHING",
                (event.chat_id, True, _RECEIVED_UPDATES, event.occurred_at),
            )
            row = self.conn.execute(
                "SELECT active,active_since,changed_at,membership_update_id,active_update_id,membership_epoch "
                "FROM coach_group_memberships WHERE chat_id=%s FOR UPDATE", (event.chat_id,),
            ).fetchone()
            if row[3] is not None and (event.occurred_at, event.update_id) <= (row[2], row[3]):
                return False
            # Permissions/promotions while present do not advance the join
            # boundary. Rejoining advances it, without reviving the old queue.
            since, active_update, epoch = row[1], row[4], row[5]
            if event.active and (not row[0] or not event.was_active):
                since = event.occurred_at
                active_update, epoch = event.update_id, epoch + 1
            self.conn.execute(
                "UPDATE coach_group_memberships SET active=%s,active_since=%s,changed_at=%s,membership_update_id=%s,"
                "active_update_id=%s,membership_epoch=%s "
                "WHERE chat_id=%s",
                (event.active, since, event.occurred_at, event.update_id, active_update, epoch, event.chat_id),
            )
            return True

    def ensure_scope(self, message: ArchiveMessage) -> bool:
        """Discover a delivered group/topic; caller must own the ingest transaction.

        When a bot was added before this webhook existed, Telegram may deliver
        queued messages without a join event, and in nonchronological order.
        Until a membership event is known, the explicit policy covers received
        updates (not historical fetching). A stable lower bound avoids silently
        losing the first message or earlier updates arriving after a later one.
        """
        self.assert_identity()
        self.conn.execute(
            "INSERT INTO coach_group_memberships(chat_id,active,active_since,changed_at) "
            "VALUES(%s,TRUE,%s,%s) ON CONFLICT DO NOTHING",
            (message.chat_id, _RECEIVED_UPDATES, message.revision_at),
        )
        row = self.conn.execute(
            "SELECT active,active_since,active_update_id FROM coach_group_memberships WHERE chat_id=%s FOR SHARE",
            (message.chat_id,),
        ).fetchone()
        if not row[0] or not self._message_after_join(message, row[1], row[2]):
            return False
        self.conn.execute(
            "INSERT INTO source_archive_scopes(chat_id,message_thread_id,topic_scope,enabled,activated_at) "
            "VALUES(%s,%s,%s,TRUE,%s) ON CONFLICT DO NOTHING",
            (message.chat_id, message.message_thread_id,
             "group" if message.message_thread_id == 1 else "forum_topic", row[1]),
        )
        return True

    def permits_received_message(self, message: ArchiveMessage) -> bool:
        """Configured-mode compatibility plus a durable membership tombstone."""
        row = self.conn.execute(
            "SELECT active,active_since,active_update_id FROM coach_group_memberships WHERE chat_id=%s FOR SHARE",
            (message.chat_id,),
        ).fetchone()
        return row is None or (row[0] and self._message_after_join(message, row[1], row[2]))

    def _message_after_join(self, message: ArchiveMessage, since: datetime, join_update_id: int | None) -> bool:
        if not message.is_edited:
            return _after_join(message.occurred_at, message.telegram_update_id, since, join_update_id)
        # An edit of an already admitted source must invalidate its old text,
        # even across a remove/rejoin, within the exact second of first join,
        # or when a pre-leave correction is delivered after the later rejoin.
        # The source repository independently orders revisions and checks the
        # author's current retention grant; this cannot roll back or resurrect
        # revoked history. Edits never create a new analysis job.
        # Unknown old messages are not imported merely because they were edited.
        key = (message.chat_id, message.message_thread_id, message.author_user_id, message.telegram_message_id)
        known = bool(self.conn.execute(
            "SELECT EXISTS(SELECT 1 FROM source_archive_messages WHERE chat_id=%s AND message_thread_id=%s "
            "AND author_user_id=%s AND telegram_message_id=%s) OR EXISTS(SELECT 1 FROM coach_message_versions "
            "WHERE chat_id=%s AND message_thread_id=%s AND author_user_id=%s AND telegram_message_id=%s)",
            (*key, *key),
        ).fetchone()[0])
        if known:
            return True
        return (_after_join(message.revision_at, message.telegram_update_id, since, join_update_id)
                and (message.occurred_at > since or join_update_id is None))

    def maintenance(self, *, transient_ttl_seconds: int = 86400, now=None) -> dict[str, int]:
        """Global transient cleanup also runs when no group remains connected."""
        if type(transient_ttl_seconds) is not int or not 60 <= transient_ttl_seconds <= 86400:
            raise ValueError("Transient text lifetime must be between one minute and 24 hours")
        at = now or datetime.now(timezone.utc)
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("A timezone-aware clock is required")
        at = at.astimezone(timezone.utc)
        with self.conn.transaction():
            self.assert_identity()
            jobs = self.conn.execute(
                "UPDATE coach_jobs SET status='expired',current_text=NULL,source_checksum=NULL,source_id=NULL,lease_token=NULL,lease_until=NULL "
                "WHERE expires_at<=%s AND status IN ('pending','processing')", (at,),
            ).rowcount
            outputs = self.conn.execute(
                "UPDATE coaching_outputs SET status=CASE WHEN status='sending' THEN 'uncertain' ELSE 'expired' END,"
                "text=NULL,source_proofs='[]'::jsonb,request_checksum=NULL,source_id=NULL,lease_token=NULL,lease_until=NULL "
                "WHERE (expires_at<=%s AND status IN ('pending','sending')) "
                "OR (status='sending' AND lease_until<%s)", (at, at),
            ).rowcount
            cancel_failed_bundle_tails(self.conn)
            self.conn.execute("UPDATE coach_message_versions SET checksum=NULL WHERE expires_at<=%s", (at,))
            self.conn.execute("DELETE FROM coach_ingest_receipts WHERE received_at<%s", (at - timedelta(days=30),))
            self.conn.execute(
                "UPDATE coach_episodes SET summary=NULL,source_proofs='[]'::jsonb,is_active=FALSE "
                "WHERE status='prepared' AND is_active AND created_at<%s",
                (at - timedelta(seconds=transient_ttl_seconds),),
            )
            return {"expired_jobs": jobs, "expired_or_uncertain_outputs": outputs}
