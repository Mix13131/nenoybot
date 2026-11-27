"""Explicit creation of one dedicated logical coach DB on an existing server.

No credentials are printed or returned. Importing this module has no effects;
the operator must call bootstrap_dedicated_database with two explicit DSNs.
Existing databases are only inspected, never dropped, migrated or reassigned.
"""
from __future__ import annotations

import re

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict


_LOCK_NAMESPACE = 1313162112
_IDENTIFIER = re.compile(r"coach_[a-z0-9_]+\Z")
_INDIRECT_OPTIONS = frozenset({"service", "servicefile", "passfile"})


class CoachDatabaseBootstrapError(RuntimeError):
    """Fixed reason code only; never a driver exception or credential-bearing DSN."""


def _connection_parameters(value: str) -> dict[str, str]:
    if not isinstance(value, str) or not value.strip():
        raise CoachDatabaseBootstrapError("explicit_database_urls_required")
    try:
        parameters = conninfo_to_dict(value)
    except Exception:
        raise CoachDatabaseBootstrapError("invalid_database_url") from None
    if not all(parameters.get(field) for field in ("host", "user", "dbname")):
        raise CoachDatabaseBootstrapError("explicit_host_user_database_required")
    if _INDIRECT_OPTIONS.intersection(parameters):
        raise CoachDatabaseBootstrapError("indirect_database_configuration_refused")
    if any("," in parameters.get(field, "") for field in ("host", "hostaddr", "port")):
        raise CoachDatabaseBootstrapError("multiple_database_servers_refused")
    # Keep the default port and passwordless local-test URLs comparable to their
    # explicit equivalents. Production DSNs carry their existing server secret.
    parameters.setdefault("port", "5432")
    parameters.setdefault("password", "")
    return parameters


def bootstrap_dedicated_database(target_url: str, bootstrap_url: str, bot_user_id: int) -> dict:
    """Create or validate only the explicitly named coach database.

    Both DSNs must describe the same server, role, credentials and connection
    options; only dbname may differ. A session advisory lock serializes creation
    across operators, and connection closure releases it on every exit path.
    """
    if type(bot_user_id) is not int or not 0 < bot_user_id < 2**63:
        raise CoachDatabaseBootstrapError("invalid_bot_identity")
    target = _connection_parameters(target_url)
    bootstrap = _connection_parameters(bootstrap_url)
    name = target["dbname"]
    if not _IDENTIFIER.fullmatch(name) or len(name.encode("utf-8")) > 63:
        raise CoachDatabaseBootstrapError("dedicated_coach_database_name_required")
    if name == bootstrap["dbname"]:
        raise CoachDatabaseBootstrapError("bootstrap_and_target_database_must_differ")
    if ({key: value for key, value in target.items() if key != "dbname"}
            != {key: value for key, value in bootstrap.items() if key != "dbname"}):
        raise CoachDatabaseBootstrapError("database_connections_must_match")

    created = False
    try:
        bootstrap_options = {**bootstrap, "connect_timeout": "5"}
        target_options = {**target, "connect_timeout": "5"}
        with psycopg.connect(**bootstrap_options, autocommit=True) as admin:
            if admin.info.dbname != bootstrap["dbname"] or admin.info.user != bootstrap["user"]:
                raise CoachDatabaseBootstrapError("bootstrap_connection_identity_mismatch")
            admin.execute("SELECT pg_advisory_lock(%s,hashtext(%s))", (_LOCK_NAMESPACE, name))
            current_role = admin.execute("SELECT current_user").fetchone()[0]
            if current_role != bootstrap["user"]:
                raise CoachDatabaseBootstrapError("bootstrap_role_mismatch")
            existing = admin.execute(
                "SELECT pg_get_userbyid(datdba),datistemplate,datallowconn "
                "FROM pg_database WHERE datname=%s", (name,),
            ).fetchone()
            if existing is not None:
                if existing != (current_role, False, True):
                    raise CoachDatabaseBootstrapError("existing_database_owner_or_state_mismatch")
            else:
                admin.execute(sql.SQL("CREATE DATABASE {} WITH TEMPLATE template0 OWNER {}").format(
                    sql.Identifier(name), sql.Identifier(current_role),
                ))
                created = True
            with psycopg.connect(**target_options, autocommit=True) as conn:
                if conn.info.dbname != name or conn.info.user != current_role:
                    raise CoachDatabaseBootstrapError("target_connection_identity_mismatch")
                # Local import avoids a module cycle when coach_admin exposes
                # this explicit operation as an operator CLI subcommand.
                from app_v2.coach_admin import CoachPreparationError, assert_dedicated_database
                try:
                    assert_dedicated_database(conn, bot_user_id)
                except CoachPreparationError:
                    raise CoachDatabaseBootstrapError("target_database_not_dedicated") from None
    except CoachDatabaseBootstrapError:
        raise
    except Exception:
        raise CoachDatabaseBootstrapError("database_bootstrap_failed") from None
    return {"database": name, "created": created}
