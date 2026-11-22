"""Explicit preparation/diagnostics for the new coach deployment.

Reads credentials and exact private scope only from COACH_* environment. It
cannot grant another participant permission. No command runs at import time.
"""
from __future__ import annotations

import argparse
import json
import re
from typing import Any

from app_v2.adapters.coach_telegram import CoachTelegramClient, CoachTelegramError
from app_v2.adapters.postgres import connect
from app_v2.coach_config import CoachConfig, CoachConfigurationError, load_coach_config
from app_v2.db.migrations import discover_migrations, run_migrations
from app_v2.repositories.coach_repo import CoachRepository
from app_v2.repositories.source_archive_repo import SourceArchiveRepository


class CoachPreparationError(RuntimeError):
    pass


def _configured(config: CoachConfig):
    if not all((config.database_url, config.bot_user_id, config.chat_id, config.topic_ids,
                config.archive_policy == "until_author_deletes")):
        raise CoachPreparationError("coach_scope_and_retention_are_not_configured")


def assert_dedicated_database(conn, bot_user_id: int) -> None:
    """Refuse reuse of a legacy/runtime DB or an already different bot identity."""
    expected = {"schema_migrations"}
    for migration in discover_migrations():
        expected.update(name.lower() for name in re.findall(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-zA-Z_][a-zA-Z0-9_]*)",
            migration.sql, re.I,
        ))
    relations = conn.execute(
        "SELECT table_schema,table_name FROM information_schema.tables "
        "WHERE table_schema NOT IN ('pg_catalog','information_schema')"
    ).fetchall()
    if any(schema != "public" or table not in expected for schema, table in relations):
        raise CoachPreparationError("database_contains_unrelated_application_relations")
    bound = False
    if conn.execute("SELECT to_regclass('coach_runtime_identity')").fetchone()[0]:
        row = conn.execute("SELECT bot_user_id FROM coach_runtime_identity WHERE singleton").fetchone()
        if row is not None:
            if int(row[0]) != bot_user_id:
                raise CoachPreparationError("database_belongs_to_another_bot")
            bound = True
    # A confirmed coach identity still does not authorize a second product's
    # legacy queues. An unbound schema must be wholly empty except migration log.
    checked = ("chats", "users", "events", "messages", "outbox") if bound else tuple(expected - {"schema_migrations"})
    for table in checked:
        if conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0]:
            # Table names are a fixed internal allowlist, not operator input.
            if conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                raise CoachPreparationError("database_contains_existing_bot_data")


def prepare(config: CoachConfig) -> dict[str, Any]:
    _configured(config)
    with connect(config.database_url, autocommit=True, connect_timeout=5) as conn:
        assert_dedicated_database(conn, config.bot_user_id)
    applied = run_migrations(config.database_url)
    with connect(config.database_url, autocommit=True, connect_timeout=5) as conn:
        with conn.transaction():
            assert_dedicated_database(conn, config.bot_user_id)
            archive = SourceArchiveRepository(conn)
            for topic in config.topic_ids:
                repo = CoachRepository(conn, chat_id=config.chat_id, message_thread_id=topic,
                                       bot_user_id=config.bot_user_id,
                                       transient_ttl_seconds=config.transient_ttl_seconds)
                repo.bind_identity()
                # A repeated deploy must not advance an existing scope's cutoff.
                row = conn.execute("SELECT enabled FROM source_archive_scopes WHERE chat_id=%s AND message_thread_id=%s",
                                   (config.chat_id, topic)).fetchone()
                if row is None:
                    archive.configure_scope(config.chat_id, topic, enabled=True)
    return {"status": "prepared", "migrations_applied": applied,
            "configured_topics": len(config.topic_ids), "participant_grants_created": 0}


def configure_telegram(config: CoachConfig, *, client=None) -> dict:
    _configured(config)
    if not config.public_base_url or not config.webhook_secret or not config.telegram_bot_token:
        raise CoachPreparationError("telegram_webhook_configuration_missing")
    owns = client is None
    client = client or CoachTelegramClient(config.telegram_bot_token)
    try:
        identity = client.get_me()
        if identity["id"] != config.bot_user_id or str(identity.get("username", "")).casefold() != (config.bot_username or "").casefold():
            raise CoachPreparationError("telegram_identity_does_not_match_configuration")
        with connect(config.database_url, autocommit=True, connect_timeout=5) as conn:
            repo = CoachRepository(conn, chat_id=config.chat_id, message_thread_id=config.topic_ids[0],
                                   bot_user_id=config.bot_user_id)
            repo.assert_identity()
        client.check_group_access(chat_id=config.chat_id, bot_user_id=config.bot_user_id,
                                  can_read_all_group_messages=identity.get("can_read_all_group_messages") is True)
        verified = client.configure_webhook(public_base_url=config.public_base_url, secret=config.webhook_secret)
        client.configure_commands()
        return {"status": "telegram_configured", **verified,
                "ordinary_message_access_verified": True, "topic_ids_need_received_message_smoke": True}
    finally:
        if owns:
            client.close()


def inspect_status(config: CoachConfig) -> dict:
    _configured(config)
    with connect(config.database_url, autocommit=True, connect_timeout=5) as conn:
        repo = CoachRepository(conn, chat_id=config.chat_id, message_thread_id=config.topic_ids[0],
                               bot_user_id=config.bot_user_id)
        repo.assert_identity()
        jobs = conn.execute("SELECT mode,status,count(*) FROM coach_jobs GROUP BY mode,status ORDER BY mode,status").fetchall()
        outputs = conn.execute("SELECT status,count(*) FROM coaching_outputs GROUP BY status ORDER BY status").fetchall()
        source_count = conn.execute("SELECT count(*) FROM source_archive_revisions WHERE available_text IS NOT NULL").fetchone()[0]
    return {"status": "ready", "enabled": config.enabled, "ai_enabled": config.ai_enabled,
            "topics": len(config.topic_ids), "archive_policy": config.archive_policy,
            "jobs": [{"mode": x[0], "status": x[1], "count": x[2]} for x in jobs],
            "outputs": [{"status": x[0], "count": x[1]} for x in outputs], "available_source_texts": source_count}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare only the separately configured coach")
    parser.add_argument("command", choices=("prepare", "setup-telegram", "status"))
    args = parser.parse_args(argv)
    try:
        config = load_coach_config()
        action = {"prepare": prepare, "setup-telegram": configure_telegram, "status": inspect_status}[args.command]
        print(json.dumps(action(config), ensure_ascii=False))
        return 0
    except (CoachPreparationError, CoachConfigurationError, CoachTelegramError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
