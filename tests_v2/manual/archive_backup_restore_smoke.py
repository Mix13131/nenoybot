"""Manual PostgreSQL dump/restore check using only a fresh local test database.

Usage from the repository root::

    NENOY_V2_ENV=test NENOY_V2_TEST_DATABASE_URL=postgresql://.../nenoy_v2_test \
      python tests_v2/manual/archive_backup_restore_smoke.py --pg-bin /path/to/pg/bin

The database must be empty, explicitly named ``nenoy_v2_test``, and on loopback.
The source database is never dropped. Only this run's temporary restore database
and dump file are removed. The verified backup is taken AFTER scoped deletion;
this does not establish deletion from older backups or a production recovery
policy. This file is deliberately not part of pytest auto-collection.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


def _local_test_connection() -> tuple[str, dict[str, str]]:
    """Reject service/host overrides and reconstruct an explicit loopback DSN."""
    if os.environ.get("NENOY_V2_ENV") != "test":
        raise ValueError("NENOY_V2_ENV must explicitly be test")
    source = os.environ.get("NENOY_V2_TEST_DATABASE_URL")
    if not source:
        raise ValueError("NENOY_V2_TEST_DATABASE_URL is required; no runtime DSN fallback")
    params = conninfo_to_dict(source)
    allowed = {"host", "hostaddr", "port", "dbname", "user", "password", "sslmode", "connect_timeout"}
    if set(params) - allowed:
        raise ValueError("Only explicit local test connection fields are accepted")
    if params.get("dbname") != "nenoy_v2_test":
        raise ValueError("Only a fresh database named nenoy_v2_test is accepted")
    host = params.get("host")
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("An explicit loopback host is required")
    canonical_host = "127.0.0.1" if host == "localhost" else host
    if params.get("hostaddr", canonical_host) != canonical_host:
        raise ValueError("hostaddr must match the explicit loopback host")
    user = params.get("user")
    if not user:
        raise ValueError("An explicit PostgreSQL test user is required")
    port = params.get("port", "5432")
    if not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise ValueError("A single valid PostgreSQL port is required")
    safe = {
        "host": canonical_host, "hostaddr": canonical_host, "port": port,
        "dbname": "nenoy_v2_test", "user": user, "connect_timeout": "5",
        "sslmode": "disable",
    }
    if "password" in params:
        safe["password"] = params["password"]
    return make_conninfo(**safe), safe


def _pg_environment(params: dict[str, str], database: str, passfile: Path) -> dict[str, str]:
    # Ambient libpq service/host/options variables must never redirect a command.
    env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    env.update({
        "PGHOST": params["host"], "PGHOSTADDR": params["hostaddr"],
        "PGPORT": params["port"], "PGDATABASE": database, "PGUSER": params["user"],
        "PGCONNECT_TIMEOUT": "5", "PGSSLMODE": "disable", "PGPASSFILE": str(passfile),
    })
    if "password" in params:
        env["PGPASSWORD"] = params["password"]
    return env


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pg-bin", required=True, type=Path, help="Directory with pg_dump and pg_restore")
    args = parser.parse_args(argv)
    source_dsn, params = _local_test_connection()
    pg_bin = args.pg_bin.expanduser().resolve()
    for name in ("pg_dump", "pg_restore"):
        if not (pg_bin / name).is_file() or not os.access(pg_bin / name, os.X_OK):
            raise ValueError(f"{name} is not executable in --pg-bin")

    # Allow execution by file path as well as by `python -m`, without installation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app_v2.adapters.telegram_webhook import normalize_update
    from app_v2.db.migrations import run_migrations
    from app_v2.repositories.source_archive_repo import SourceArchiveRepository

    at = datetime(2026, 11, 1, tzinfo=timezone.utc)
    seed = 8_000_000_000 + int(uuid4().hex[:10], 16)
    chat, author, topic = -seed, seed + 1, 17

    def normalized_message(n: int, text: str, *, edited: bool = False):
        message = {
            "message_id": 1 if edited else n, "date": int(at.timestamp()),
            "from": {"id": author, "is_bot": False, "first_name": "Synthetic backup test"},
            "chat": {"id": chat, "type": "supergroup", "is_forum": True},
            "message_thread_id": topic, "is_topic_message": True, "text": text,
        }
        if edited:
            message["edit_date"] = int((at + timedelta(minutes=1)).timestamp())
        normalized = normalize_update({"update_id": seed + n,
                                       "edited_message" if edited else "message": message})
        _check(normalized is not None, "Synthetic message normalization failed")
        return normalized

    first = normalized_message(1, "SYNTHETIC_DELETED_SOURCE_V1")
    second = normalized_message(2, "SYNTHETIC_PRESERVED_SOURCE_V1")
    unseen = normalized_message(4, "SYNTHETIC_DELAYED_DELETED_SOURCE")
    target = "nenoy_archive_restore_test_" + uuid4().hex
    target_created = False
    result: dict[str, object] = {"status": "PASS", "backup_taken_after_deletion": True,
                                "older_backups_erased": False, "source_database_removed": False}

    # Keep the control connection on the verified source, never on postgres or
    # an arbitrary administrative database. All mutation identifiers are fixed
    # or generated here; none come from unvalidated SQL/CLI text.
    with psycopg.connect(source_dsn, autocommit=True) as admin:
        database, server = admin.execute("SELECT current_database(), host(inet_server_addr())").fetchone()
        _check(database == "nenoy_v2_test" and ipaddress.ip_address(server).is_loopback,
               "The connected server is not the selected loopback test database")
        existing = admin.execute(
            """SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
               WHERE c.relkind IN ('r','p','v','m','f')
                 AND n.nspname NOT IN ('pg_catalog','information_schema')
                 AND n.nspname NOT LIKE 'pg_toast%')"""
        ).fetchone()[0]
        _check(not existing, "Use a fresh empty test database; refusing to dump existing data")
        result["applied_migrations"] = run_migrations(source_dsn)
        with psycopg.connect(source_dsn) as conn:
            repo = SourceArchiveRepository(conn)
            repo.configure_scope(chat, topic, enabled=True, activated_at=at - timedelta(days=1))
            repo.set_permissions(chat_id=chat, message_thread_id=topic, author_user_id=author,
                                 actor_user_id=author, retain_source=True, now=at - timedelta(hours=1))
            deleted_capture = repo.capture(first)
            kept_capture = repo.capture(second)
            _check(deleted_capture.status == kept_capture.status == "archived", "Synthetic capture failed")
            receipt = repo.forget_sources(chat_id=chat, message_thread_id=topic, author_user_id=author,
                                          actor_user_id=author, telegram_message_id=1)
            _check(receipt.messages == receipt.revisions == 1 and not receipt.backups_erased,
                   "Scoped deletion receipt is incorrect")
            unseen_receipt = repo.forget_sources(
                chat_id=chat, message_thread_id=topic, author_user_id=author,
                actor_user_id=author, telegram_message_id=4,
            )
            _check(unseen_receipt.messages == unseen_receipt.revisions == 0
                   and unseen_receipt.deletion_intent_recorded,
                   "Deletion of an unseen message did not preserve intent")

        try:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target)))
            target_created = True
            target_params = {**params, "dbname": target}
            target_dsn = make_conninfo(**target_params)
            with tempfile.TemporaryDirectory(prefix="nenoy_archive_restore_") as temporary:
                directory = Path(temporary)
                dump = directory / "synthetic_after_delete.dump"
                passfile = directory / "empty.pgpass"
                passfile.touch(mode=0o600)
                dump.touch(mode=0o600)
                subprocess.run(
                    [str(pg_bin / "pg_dump"), "--format=custom", "--no-owner", "--no-privileges",
                     "--file", str(dump)], check=True, timeout=60,
                    env=_pg_environment(params, "nenoy_v2_test", passfile),
                )
                result["dump_bytes"] = dump.stat().st_size
                subprocess.run(
                    [str(pg_bin / "pg_restore"), "--exit-on-error", "--no-owner", "--no-privileges",
                     "--dbname", target, str(dump)], check=True, timeout=60,
                    env=_pg_environment(params, target, passfile),
                )
                with psycopg.connect(target_dsn) as restored:
                    repo = SourceArchiveRepository(restored)
                    sources = repo.query_sources(
                        chat_id=chat, message_thread_id=topic, author_user_id=author,
                        actor_user_id=author, since=at - timedelta(days=1), until=at + timedelta(days=1),
                    )
                    _check(len(sources) == 1 and sources[0].available_text == second.message["text"]
                           and sources[0].source_id == kept_capture.source_id
                           and sources[0].checksum == kept_capture.checksum,
                           "Restored active source text/provenance/checksum differs")
                    tombstone = restored.execute(
                        """SELECT source_status, available_text, checksum, media_reference,
                                  is_deleted, deleted_at IS NOT NULL
                           FROM source_archive_revisions WHERE source_id=%s""",
                        (deleted_capture.source_id,),
                    ).fetchone()
                    _check(tombstone == ("deleted", None, None, {}, True, True),
                           "Deleted revision was not restored as a scrubbed tombstone")
                    parent = restored.execute(
                        "SELECT is_deleted, latest_source_id FROM source_archive_messages "
                        "WHERE chat_id=%s AND telegram_message_id=1", (chat,),
                    ).fetchone()
                    _check(parent == (True, None), "Restored deleted parent is not tombstoned")
                    replay = repo.capture(first)
                    late_edit = repo.capture(normalized_message(3, "SYNTHETIC_LATE_EDIT", edited=True))
                    delayed = repo.capture(unseen)
                    _check(replay.status == late_edit.status == delayed.status == "deleted",
                           "Replay or delayed delivery resurrected a deleted source")
                    _check(restored.execute(
                        "SELECT count(*) FROM source_archive_revisions WHERE chat_id=%s AND source_status='active'",
                        (chat,),
                    ).fetchone()[0] == 1, "Replay changed the active-source count")
                    result.update({"restored_active_sources": 1, "active_text_and_checksum_preserved": True,
                                   "scrubbed_tombstone_preserved": True, "replay_status": replay.status,
                                   "late_edit_status": late_edit.status,
                                   "unseen_deletion_marker_preserved": delayed.status == 'deleted'})
            result["temporary_dump_removed"] = not directory.exists()
        finally:
            if target_created:
                # Never drop the source or another run's database, even on failure.
                _check(target != database and target.startswith("nenoy_archive_restore_test_"),
                       "Unexpected restore target during cleanup")
                admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(target)))
                result["target_database_removed"] = not admin.execute(
                    "SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=%s)", (target,),
                ).fetchone()[0]
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
