"""Explicit preparation/diagnostics for the new coach deployment.

Reads credentials and exact private scope only from COACH_* environment. It
cannot grant another participant permission. No command runs at import time.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
import re
from typing import Any
from uuid import uuid4

from app_v2.adapters.coach_telegram import CoachTelegramClient, CoachTelegramError
from app_v2.adapters.postgres import connect
from app_v2.coach_config import CoachConfig, CoachConfigurationError, load_coach_config
from app_v2.coach_database import CoachDatabaseBootstrapError
from app_v2.db.migrations import discover_migrations, run_migrations
from app_v2.repositories.coach_scope_repo import CoachScopeRepository
from app_v2.repositories.coach_budget_repo import CoachBudgetRepository
from app_v2.repositories.source_archive_repo import SourceArchiveRepository
from app_v2.services.coach_ai import CoachAI, CoachAIError, OpenAIResponsesProvider


class CoachPreparationError(RuntimeError):
    pass


def _configured(config: CoachConfig):
    if not all((config.database_url, config.bot_user_id, config.scope_configured,
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
            scopes = CoachScopeRepository(conn, bot_user_id=config.bot_user_id)
            scopes.bind_identity()
            archive = SourceArchiveRepository(conn)
            topics = config.topic_ids if config.group_mode == "configured" else ()
            for topic in topics:
                # A repeated deploy must not advance an existing scope's cutoff.
                row = conn.execute("SELECT enabled FROM source_archive_scopes WHERE chat_id=%s AND message_thread_id=%s",
                                   (config.chat_id, topic)).fetchone()
                if row is None:
                    archive.configure_scope(config.chat_id, topic, enabled=True)
    return {"status": "prepared", "migrations_applied": applied,
            "configured_topics": len(topics), "group_mode": config.group_mode,
            "participant_grants_created": 0}


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
            CoachScopeRepository(conn, bot_user_id=config.bot_user_id).assert_identity()
        if config.group_mode == "joined_groups":
            identity = client.configure_group_reading()
            if (not isinstance(identity, dict) or identity.get("id") != config.bot_user_id
                    or str(identity.get("username", "")).casefold() != (config.bot_username or "").casefold()):
                raise CoachPreparationError("telegram_identity_does_not_match_configuration")
        can_read_all = identity.get("can_read_all_group_messages") is True
        if config.group_mode == "joined_groups" and not can_read_all:
            raise CoachPreparationError("telegram_group_reading_not_verified")
        if config.chat_id is not None:
            client.check_group_access(chat_id=config.chat_id, bot_user_id=config.bot_user_id,
                                      can_read_all_group_messages=can_read_all)
        elif not can_read_all:
            raise CoachPreparationError("disable_group_privacy_or_configure_a_group_for_access_check")
        verified = client.configure_webhook(public_base_url=config.public_base_url, secret=config.webhook_secret)
        client.configure_commands()
        return {"status": "telegram_configured", **verified,
                "ordinary_message_access_verified": True, "group_mode": config.group_mode,
                "group_privacy_disabled": can_read_all,
                "all_joined_groups_need_received_message_smoke": config.group_mode == "joined_groups",
                "topic_ids_need_received_message_smoke": config.group_mode == "configured"}
    finally:
        if owns:
            client.close()


def inspect_status(config: CoachConfig) -> dict:
    _configured(config)
    with connect(config.database_url, autocommit=True, connect_timeout=5) as conn:
        registry = CoachScopeRepository(conn, bot_user_id=config.bot_user_id)
        registry.assert_identity()
        scopes = [scope for scope in registry.list_scopes(include_disabled=True)
                  if config.permits_scope(*scope)]
        jobs = conn.execute("SELECT mode,status,count(*) FROM coach_jobs GROUP BY mode,status ORDER BY mode,status").fetchall()
        outputs = conn.execute("SELECT status,count(*) FROM coaching_outputs GROUP BY status ORDER BY status").fetchall()
        source_count = conn.execute("SELECT count(*) FROM source_archive_revisions WHERE available_text IS NOT NULL").fetchone()[0]
    return {"status": "ready", "enabled": config.enabled, "ai_enabled": config.ai_enabled,
            "topics": len(scopes), "groups": len({chat for chat, _ in scopes}),
            "group_mode": config.group_mode, "archive_policy": config.archive_policy,
            "jobs": [{"mode": x[0], "status": x[1], "count": x[2]} for x in jobs],
            "outputs": [{"status": x[0], "count": x[1]} for x in outputs], "available_source_texts": source_count}


def inspect_statistics(config: CoachConfig, *, since: datetime | None = None,
                       until: datetime | None = None) -> dict:
    """Read-only counters; no conversation text, participant IDs or credentials."""
    status = inspect_status(config)
    budget = CoachBudgetRepository(config.database_url)
    return {"status": "statistics", "runtime": status,
            "configured_budget_mode": config.ai_budget_mode,
            "today_utc": budget.snapshot(),
            "usage": budget.usage_stats(since=since, until=until),
            "cost_basis": "configured_token_rates_before_cache_discounts"}


def bootstrap_database(config: CoachConfig) -> dict:
    """Explicit deployment action; the server and worker never create databases."""
    from app_v2.coach_database import bootstrap_dedicated_database

    _configured(config)
    result = bootstrap_dedicated_database(
        config.database_url, os.environ.get("COACH_DATABASE_BOOTSTRAP_URL", ""),
        config.bot_user_id,
    )
    return {"status": "database_bootstrapped", **result}


def smoke_ai(config: CoachConfig, *, provider=None) -> dict:
    """One explicitly invoked synthetic request, accounted like a real request.

    Never sends Telegram messages or reads participant history. This command is
    for deployment verification and is not called by an import or the worker.
    """
    _configured(config)
    if not config.ai_enabled:
        raise CoachPreparationError("ai_is_not_enabled")
    with connect(config.database_url, autocommit=True, connect_timeout=5) as conn:
        CoachScopeRepository(conn, bot_user_id=config.bot_user_id).assert_identity()
    ai = CoachAI(CoachBudgetRepository(config.database_url),
                 provider or OpenAIResponsesProvider(config.openai_api_key), config.ai_config())
    result = ai.request_json(
        operation_id="operator-smoke:" + str(uuid4()), purpose="operator_smoke",
        messages=[{"role": "user", "content": 'Synthetic connection test. Return JSON {"ok": true}.'}],
        schema={"type": "object", "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"], "additionalProperties": False},
    )
    if set(result.parsed) != {"ok"} or result.parsed["ok"] is not True:
        raise CoachPreparationError("synthetic_ai_response_failed_validation")
    return {"status": "ai_verified", "model": config.ai_model,
            "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
            "cost_microusd": result.cost_microusd, "telegram_messages_sent": 0}


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise argparse.ArgumentTypeError("Use an ISO 8601 timestamp with timezone") from None
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("A timezone is required")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare only the separately configured coach")
    parser.add_argument("command", choices=("bootstrap-database", "prepare", "smoke-ai", "setup-telegram", "status", "stats"))
    parser.add_argument("--since", type=_timestamp, help="Inclusive usage interval start (stats only)")
    parser.add_argument("--until", type=_timestamp, help="Exclusive usage interval end (stats only)")
    args = parser.parse_args(argv)
    if args.command != "stats" and (args.since is not None or args.until is not None):
        parser.error("--since/--until apply only to stats")
    try:
        config = load_coach_config()
        if args.command == "stats":
            result = inspect_statistics(config, since=args.since, until=args.until)
        else:
            action = {"bootstrap-database": bootstrap_database, "prepare": prepare,
                      "smoke-ai": smoke_ai, "setup-telegram": configure_telegram,
                      "status": inspect_status}[args.command]
            result = action(config)
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    except (CoachPreparationError, CoachConfigurationError, CoachTelegramError,
            CoachDatabaseBootstrapError, CoachAIError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
