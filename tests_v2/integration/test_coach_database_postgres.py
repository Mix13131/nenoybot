"""Explicit logical DB bootstrap, using only disposable loopback test databases."""
from concurrent.futures import ThreadPoolExecutor
import os
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
import pytest

from app_v2.coach_admin import prepare
from app_v2.coach_config import CoachConfig
from app_v2.coach_database import CoachDatabaseBootstrapError, bootstrap_dedicated_database


BOT = 907070001


@pytest.fixture
def database_target():
    bootstrap = os.getenv("NENOY_V2_TEST_DATABASE_URL")
    if not bootstrap:
        pytest.skip("NENOY_V2_TEST_DATABASE_URL is not configured")
    assert conninfo_to_dict(bootstrap).get("host") in {"127.0.0.1", "localhost", "::1"}
    name = "coach_bootstrap_" + uuid4().hex
    target = make_conninfo(bootstrap, dbname=name)
    with psycopg.connect(bootstrap, autocommit=True) as admin:
        try:
            yield bootstrap, target, name, admin
        finally:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


def test_new_database_is_separate_empty_owned_and_idempotent(database_target):
    bootstrap, target, name, admin = database_target
    initial_database = admin.info.dbname
    assert bootstrap_dedicated_database(target, bootstrap, BOT) == {"database": name, "created": True}
    assert bootstrap_dedicated_database(target, bootstrap, BOT) == {"database": name, "created": False}
    assert admin.info.dbname == initial_database != name
    assert admin.execute(
        "SELECT pg_get_userbyid(datdba)=current_user FROM pg_database WHERE datname=%s", (name,)
    ).fetchone() == (True,)
    with psycopg.connect(target, autocommit=True) as conn:
        assert conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"
        ).fetchone() == (0,)


def test_concurrent_bootstrap_creates_database_once(database_target):
    bootstrap, target, name, _ = database_target
    with ThreadPoolExecutor(max_workers=2) as executor:
        attempts = [executor.submit(bootstrap_dedicated_database, target, bootstrap, BOT) for _ in range(2)]
        results = [future.result(timeout=15) for future in attempts]
    assert sorted(result["created"] for result in results) == [False, True]
    assert {result["database"] for result in results} == {name}


def test_existing_unrelated_database_is_refused_without_changing_its_data(database_target):
    bootstrap, target, name, admin = database_target
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    with psycopg.connect(target, autocommit=True) as conn:
        conn.execute("CREATE TABLE unrelated_owner_data(value TEXT NOT NULL)")
        conn.execute("INSERT INTO unrelated_owner_data VALUES('synthetic owner row')")
        with pytest.raises(CoachDatabaseBootstrapError, match="target_database_not_dedicated"):
            bootstrap_dedicated_database(target, bootstrap, BOT)
        assert conn.execute("SELECT value FROM unrelated_owner_data").fetchall() == [("synthetic owner row",)]
        assert conn.execute("SELECT to_regclass('coach_runtime_identity')").fetchone() == (None,)


def test_prepared_database_is_accepted_only_for_its_existing_bot_identity(database_target):
    bootstrap, target, name, _ = database_target
    bootstrap_dedicated_database(target, bootstrap, BOT)
    prepare(CoachConfig(environment="test", database_url=target,
                        bot_user_id=BOT, group_mode="joined_groups"))
    assert bootstrap_dedicated_database(target, bootstrap, BOT) == {"database": name, "created": False}
    with pytest.raises(CoachDatabaseBootstrapError, match="target_database_not_dedicated"):
        bootstrap_dedicated_database(target, bootstrap, BOT + 1)
    with psycopg.connect(target, autocommit=True) as conn:
        assert conn.execute("SELECT bot_user_id FROM coach_runtime_identity").fetchone() == (BOT,)


def test_existing_target_owned_by_another_role_is_not_reassigned(database_target):
    bootstrap, target, name, admin = database_target
    role = "coach_bootstrap_role_" + uuid4().hex
    current_role = admin.execute("SELECT current_user").fetchone()[0]
    admin.execute(sql.SQL("CREATE ROLE {}").format(sql.Identifier(role)))
    try:
        admin.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(name), sql.Identifier(role)))
        with pytest.raises(CoachDatabaseBootstrapError, match="owner_or_state_mismatch"):
            bootstrap_dedicated_database(target, bootstrap, BOT)
        assert admin.execute(
            "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s", (name,)
        ).fetchone() == (role,)
    finally:
        admin.execute(sql.SQL("ALTER DATABASE {} OWNER TO {}").format(sql.Identifier(name), sql.Identifier(current_role)))
        admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


SYNTHETIC_BOOTSTRAP = "host=127.0.0.1 port=5432 user=synthetic password=synthetic_secret dbname=postgres"


@pytest.mark.parametrize("name", [
    "postgres", "template0", "template1", "nenoy", "coach_", "coach_bad-name", "coach_Пилот",
    "coach_" + "a" * 58, 'coach_injection"; DROP DATABASE postgres; --',
])
def test_unsafe_or_non_coach_database_name_is_rejected_before_connect(monkeypatch, name):
    def never_connect(*args, **kwargs):
        raise AssertionError("Invalid target must not open a connection")
    monkeypatch.setattr("app_v2.coach_database.psycopg.connect", never_connect)
    with pytest.raises(CoachDatabaseBootstrapError, match="database_name_required"):
        bootstrap_dedicated_database(make_conninfo(SYNTHETIC_BOOTSTRAP, dbname=name), SYNTHETIC_BOOTSTRAP, BOT)


@pytest.mark.parametrize("changes", [
    {"host": "other.invalid"}, {"port": "5440"}, {"user": "other_user"},
    {"password": "different_synthetic_secret"}, {"sslmode": "disable"},
    {"options": "-c search_path=pg_catalog"}, {"application_name": "different_application"},
])
def test_connection_identity_or_other_options_cannot_differ(monkeypatch, changes):
    calls = []
    monkeypatch.setattr("app_v2.coach_database.psycopg.connect", lambda *args, **kwargs: calls.append(kwargs))
    target = make_conninfo(SYNTHETIC_BOOTSTRAP, dbname="coach_synthetic", **changes)
    with pytest.raises(CoachDatabaseBootstrapError, match="connections_must_match") as failure:
        bootstrap_dedicated_database(target, SYNTHETIC_BOOTSTRAP, BOT)
    assert calls == [] and "synthetic_secret" not in str(failure.value)


def test_bootstrap_database_cannot_itself_be_the_target(monkeypatch):
    calls = []
    monkeypatch.setattr("app_v2.coach_database.psycopg.connect", lambda *args, **kwargs: calls.append(kwargs))
    same = make_conninfo(SYNTHETIC_BOOTSTRAP, dbname="coach_synthetic")
    with pytest.raises(CoachDatabaseBootstrapError, match="must_differ"):
        bootstrap_dedicated_database(same, same, BOT)
    assert calls == []


@pytest.mark.parametrize("changes", [{"service": "synthetic"}, {"passfile": "/synthetic/file"},
                                     {"host": "127.0.0.1,other.invalid"}, {"port": "5432,5440"}])
def test_indirect_or_multiserver_connections_are_refused(monkeypatch, changes):
    calls = []
    monkeypatch.setattr("app_v2.coach_database.psycopg.connect", lambda *args, **kwargs: calls.append(kwargs))
    bootstrap = make_conninfo(SYNTHETIC_BOOTSTRAP, **changes)
    target = make_conninfo(bootstrap, dbname="coach_synthetic")
    with pytest.raises(CoachDatabaseBootstrapError, match="refused"):
        bootstrap_dedicated_database(target, bootstrap, BOT)
    assert calls == []


def test_driver_failure_never_exposes_url_credentials_or_exception_chain(monkeypatch):
    def fail_connect(*args, **kwargs):
        raise RuntimeError("synthetic_secret raw private connection details")
    monkeypatch.setattr("app_v2.coach_database.psycopg.connect", fail_connect)
    with pytest.raises(CoachDatabaseBootstrapError) as failure:
        bootstrap_dedicated_database(
            make_conninfo(SYNTHETIC_BOOTSTRAP, dbname="coach_synthetic"), SYNTHETIC_BOOTSTRAP, BOT)
    assert str(failure.value) == "database_bootstrap_failed"
    assert failure.value.__suppress_context__ is True
