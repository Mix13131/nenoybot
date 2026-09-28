from __future__ import annotations

import json
import os

from app_v2.adapters.postgres import connect
from app_v2.db.migrations import run_migrations
from app_v2.domain.group_defaults import new_group_profile


class LegacyReconciliationError(RuntimeError):
    """Safe reason code only: no group title, ID, message or credentials."""


def reconcile_legacy_group(
    conn,
    *,
    exact_title: str,
    telegram_chat_id: str | None = None,
    initialize_empty_profile: bool = False,
) -> dict[str, object]:
    """Operator-authorized, one-time transition of one uniquely named legacy row.

    Must be called on an idle connection. A short table lock makes the exact
    title uniqueness check atomic with respect to concurrent ingest/renames.
    Neither title nor chat ID comes from Telegram message text.
    """
    title = exact_title.strip()
    if not title:
        raise LegacyReconciliationError("empty_title")
    with conn.transaction():
        conn.execute("SET LOCAL lock_timeout = '10s'")
        conn.execute("LOCK TABLE chats IN SHARE ROW EXCLUSIVE MODE")
        rows = conn.execute(
            """
            SELECT id, telegram_chat_id, is_whitelisted, is_active,
                   group_access_blocked, group_legacy_reconcile_pending,
                   group_profile
            FROM chats
            WHERE chat_type='group' AND BTRIM(COALESCE(title, ''))=%s
            FOR UPDATE
            """,
            (title,),
        ).fetchall()
        if len(rows) != 1:
            raise LegacyReconciliationError("not_found" if not rows else "ambiguous_title")
        row = rows[0]
        if telegram_chat_id is not None and str(row[1]) != str(telegram_chat_id):
            raise LegacyReconciliationError("id_mismatch")
        if not row[3]:
            raise LegacyReconciliationError("inactive_group")
        if row[2] and not row[4] and not row[5]:
            return {"status": "already_active", "active": True, "whitelisted": True}
        if row[2] or not row[4] or not row[5]:
            raise LegacyReconciliationError("not_pending_legacy_denial")
        initialized = initialize_empty_profile and not row[6]
        profile = new_group_profile() if initialized else dict(row[6] or {})
        changed = conn.execute(
            """
            UPDATE chats
            SET is_whitelisted=TRUE, group_access_blocked=FALSE,
                group_legacy_reconcile_pending=FALSE,
                group_profile=%s::jsonb, updated_at=CURRENT_TIMESTAMP
            WHERE id=%s AND telegram_chat_id=%s AND chat_type='group'
              AND BTRIM(COALESCE(title, ''))=%s
              AND is_active=TRUE AND is_whitelisted=FALSE
              AND group_access_blocked=TRUE AND group_legacy_reconcile_pending=TRUE
            RETURNING id
            """,
            (json.dumps(profile, ensure_ascii=False), int(row[0]), int(row[1]), title),
        ).fetchone()
        if changed is None:
            raise LegacyReconciliationError("state_changed")
    return {
        "status": "reconciled", "active": True, "whitelisted": True,
        "profile_initialized": initialized,
    }


def main() -> int:
    try:
        applied = run_migrations()
        with connect() as conn:
            # Fail the deployment if the required transition schema is absent.
            conn.execute(
                "SELECT group_access_blocked, group_legacy_reconcile_pending FROM chats LIMIT 0"
            )
            conn.commit()
            title = (os.getenv("NENOY_V2_RECONCILE_LEGACY_GROUP_TITLE") or "").strip()
            result: dict[str, object] = {"status": "not_requested"}
            if title:
                result = reconcile_legacy_group(
                    conn, exact_title=title,
                    telegram_chat_id=(os.getenv("NENOY_V2_RECONCILE_LEGACY_GROUP_ID") or "").strip() or None,
                    initialize_empty_profile=(os.getenv("NENOY_V2_RECONCILE_INITIALIZE_EMPTY_PROFILE") or "").lower() == "true",
                )
        print(json.dumps({"ok": True, "migrations_applied": applied, "schema_ready": True, "reconciliation": result}))
        return 0
    except LegacyReconciliationError as exc:
        print(json.dumps({"ok": False, "reason": str(exc)}))
        return 1
    except Exception as exc:
        # SQL/connection errors may contain secret DSNs or private values.
        print(json.dumps({"ok": False, "reason": "prepare_failed", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
