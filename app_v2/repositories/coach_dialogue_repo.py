"""Scoped continuity from real deliveries and the author's own archived messages.

There is no transcript import from Telegram's nested reply/quote payload. Bot
words remain labeled assistant context, linked to raw sources, and are never
eligible evidence for extracting a factual memory card.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re

from app_v2.adapters.telegram_archive import group_delivery_matches_thread
from app_v2.domain.coach import SourceProof


def _now(value=None):
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("An aware conversation timestamp is required")
    return value.astimezone(timezone.utc)


class CoachDialogueRepository:
    def __init__(self, conn, *, chat_id: int, message_thread_id: int,
                 bot_user_id: int, transient_ttl_seconds: int = 86400):
        self.conn, self.chat_id, self.message_thread_id = conn, chat_id, message_thread_id
        self.bot_user_id = bot_user_id

    def _permission(self, author: int, *, lock=False):
        if type(author) is not int or author <= 0:
            return None
        row = self.conn.execute(
            "SELECT c.version,p.memory_epoch FROM source_archive_consents c "
            "JOIN source_archive_scopes s USING(chat_id,message_thread_id) "
            "JOIN coach_participant_state p USING(chat_id,message_thread_id,author_user_id) "
            "WHERE c.chat_id=%s AND c.message_thread_id=%s AND c.author_user_id=%s "
            "AND s.enabled AND c.retain_source AND c.historical_inference "
            + ("FOR SHARE OF s FOR UPDATE OF c,p" if lock else ""),
            (self.chat_id,self.message_thread_id,author),
        ).fetchone()
        return row

    def _proofs(self, author: int, value, version: int):
        try:
            proofs = tuple(p if isinstance(p,SourceProof) else SourceProof(**p) for p in value)
        except (TypeError,ValueError):
            return ()
        if (not proofs or len(proofs)>200 or len({p.source_id for p in proofs})!=len(proofs)
                or any(not isinstance(p.source_id,str) or not isinstance(p.checksum,str)
                       or not re.fullmatch(r"[a-f0-9]{64}",p.checksum)
                       or type(p.access_version) is not int or not 0 < p.access_version <= version
                       for p in proofs)):
            return ()
        rows = self.conn.execute(
            "SELECT r.source_id,r.checksum FROM source_archive_revisions r "
            "JOIN source_archive_messages m ON m.chat_id=r.chat_id AND m.telegram_message_id=r.telegram_message_id "
            "WHERE r.chat_id=%s AND r.message_thread_id=%s AND r.author_user_id=%s "
            "AND r.source_id=ANY(%s) AND r.source_status='active' AND NOT m.is_deleted "
            "AND m.latest_source_id=r.source_id AND NOT r.contains_external_content "
            "AND r.available_text IS NOT NULL",
            (self.chat_id,self.message_thread_id,author,[p.source_id for p in proofs]),
        ).fetchall()
        actual=dict(rows)
        if any(actual.get(p.source_id)!=p.checksum for p in proofs):
            return ()
        return tuple(SourceProof(p.source_id,p.checksum,version) for p in proofs)

    def record_sent(self, output, result: dict, *, now=None) -> bool:
        """Called inside guarded delivery, only after a confirmed network result.

        Verify the claimed outbox row as well: a stale callback cannot recreate
        deleted context. The caller commits this with the delivery receipt.
        """
        if (output.chat_id,output.message_thread_id)!=(self.chat_id,self.message_thread_id):
            return False
        if output.mode=="control" or output.kind!="reply" or not output.source_proofs:
            return False
        telegram_id=result.get("message_id")
        if (type(telegram_id) is not int or telegram_id<=0 or
                result.get("chat",{}).get("id")!=self.chat_id or
                not group_delivery_matches_thread(result,self.message_thread_id)):
            return False
        with self.conn.transaction():
            identity=self.conn.execute("SELECT bot_user_id FROM coach_runtime_identity WHERE singleton").fetchone()
            if identity!=(self.bot_user_id,):
                return False
            permission=self._permission(output.author_user_id,lock=True)
            if not permission or permission[0]!=output.consent_version:
                return False
            expected=[{"source_id":p.source_id,"checksum":p.checksum,"access_version":p.access_version}
                      for p in output.source_proofs]
            row=self.conn.execute(
                "SELECT text,source_proofs FROM coaching_outputs WHERE id=%s AND status='sending' "
                "AND lease_token=%s AND chat_id=%s AND message_thread_id=%s AND author_user_id=%s "
                "AND consent_version=%s AND lease_until>%s AND expires_at>%s FOR UPDATE",
                (output.id,output.lease_token,self.chat_id,self.message_thread_id,output.author_user_id,
                 output.consent_version,_now(now),_now(now)),
            ).fetchone()
            if not row or row[0]!=output.text or row[1]!=expected:
                return False
            proofs=self._proofs(output.author_user_id,output.source_proofs,permission[0])
            if not proofs:
                return False
            self.conn.execute(
                "INSERT INTO coach_dialogue_turns(output_id,chat_id,message_thread_id,author_user_id,"
                "telegram_message_id,text,source_proofs,permission_version,memory_epoch,occurred_at) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s) ON CONFLICT DO NOTHING",
                (output.id,self.chat_id,self.message_thread_id,output.author_user_id,telegram_id,
                 output.text,json.dumps(expected),permission[0],permission[1],_now(now)),
            )
            return True

    def context(self, author_user_id: int, *, max_bytes: int = 12000, before=None) -> list[dict]:
        if type(max_bytes) is not int or not 1<=max_bytes<=100_000:
            raise ValueError("A bounded conversation byte budget is required")
        until=_now(before)
        with self.conn.transaction():
            identity=self.conn.execute("SELECT bot_user_id FROM coach_runtime_identity WHERE singleton").fetchone()
            if identity!=(self.bot_user_id,):
                return []
            permission=self._permission(author_user_id,lock=True)
            if not permission:
                return []
            version,epoch=permission
            assistant=self.conn.execute(
                "SELECT d.text,d.source_proofs,d.telegram_message_id,d.occurred_at FROM coach_dialogue_turns d "
                "JOIN coaching_outputs o ON o.id=d.output_id AND o.status='sent' "
                "WHERE d.chat_id=%s AND d.message_thread_id=%s AND d.author_user_id=%s "
                "AND d.memory_epoch=%s AND d.text IS NOT NULL AND d.occurred_at<%s "
                "ORDER BY d.occurred_at DESC,d.telegram_message_id DESC LIMIT 64",
                (self.chat_id,self.message_thread_id,author_user_id,epoch,until),
            ).fetchall()
            user=self.conn.execute(
                "SELECT r.available_text,r.source_id,r.checksum,r.telegram_message_id,r.occurred_at "
                "FROM source_archive_revisions r JOIN source_archive_messages m "
                "ON m.chat_id=r.chat_id AND m.telegram_message_id=r.telegram_message_id "
                "WHERE r.chat_id=%s AND r.message_thread_id=%s AND r.author_user_id=%s "
                "AND m.latest_source_id=r.source_id AND NOT m.is_deleted AND r.source_status='active' "
                "AND NOT r.contains_external_content AND r.available_text IS NOT NULL AND r.occurred_at<%s "
                "ORDER BY r.occurred_at DESC,r.telegram_message_id DESC LIMIT 64",
                (self.chat_id,self.message_thread_id,author_user_id,until),
            ).fetchall()
            candidates=[]
            for text,raw,telegram_id,at in assistant:
                proofs=self._proofs(author_user_id,raw,version)
                if proofs:
                    candidates.append(dict(role="assistant",text=text,source_proofs=proofs,
                                           telegram_message_id=telegram_id,occurred_at=at))
            for text,source_id,checksum,telegram_id,at in user:
                candidates.append(dict(role="user",text=text,
                                       source_proofs=(SourceProof(source_id,checksum,version),),
                                       telegram_message_id=telegram_id,occurred_at=at))
            selected,used=[],0
            for item in sorted(candidates,key=lambda x:(x["occurred_at"],x["telegram_message_id"]),reverse=True):
                size=len(item["text"].encode("utf-8"))
                if used+size>max_bytes:
                    continue
                selected.append(item)
                used+=size
            return list(reversed(selected))
