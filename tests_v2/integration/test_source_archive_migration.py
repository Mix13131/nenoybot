"""Synthetic PostgreSQL migration atomicity and explicit downgrade rehearsal."""
from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest


_ROOT = Path(__file__).resolve().parents[2]
_SQL = _ROOT / 'app_v2/db/migrations/0010_source_archive.sql'
_TABLES = (
    'source_archive_scopes', 'source_archive_consents', 'source_archive_consent_versions',
    'source_archive_deletions', 'source_archive_messages', 'source_archive_revisions',
)


@pytest.fixture
def isolated_schema():
    url = os.getenv('NENOY_V2_TEST_DATABASE_URL')
    if not url:
        pytest.skip('NENOY_V2_TEST_DATABASE_URL is not configured')
    pg = pytest.importorskip('psycopg')
    from psycopg import sql
    schema = 'archive_migration_' + uuid4().hex
    with pg.connect(url, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
        try:
            yield conn
        finally:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def test_archive_migration_rollback_leaves_no_half_created_tables(isolated_schema):
    conn = isolated_schema
    class SimulatedMigrationFailure(Exception):
        pass
    with pytest.raises(SimulatedMigrationFailure):
        with conn.transaction():
            conn.execute(_SQL.read_text(), prepare=False)
            assert all(conn.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0] for table in _TABLES)
            raise SimulatedMigrationFailure()
    assert all(conn.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0] is None for table in _TABLES)
    # A fresh retry works; PostgreSQL rolled back DDL as well as data.
    with conn.transaction():
        conn.execute(_SQL.read_text(), prepare=False)
    assert conn.execute('SELECT COUNT(*) FROM source_archive_scopes').fetchone()[0] == 0
    assert conn.execute('SELECT COUNT(*) FROM source_archive_consents').fetchone()[0] == 0


def test_archive_explicit_empty_schema_downgrade_preserves_unrelated_tables(isolated_schema):
    conn = isolated_schema
    conn.execute('CREATE TABLE legacy_probe(value TEXT)')
    conn.execute("INSERT INTO legacy_probe VALUES ('synthetic unchanged')")
    with conn.transaction():
        conn.execute(_SQL.read_text(), prepare=False)
    # Only a synthetic, empty-schema rehearsal. Runtime rollback is flag-off,
    # leaving retained sources intact; production destruction is not authorized.
    with conn.transaction():
        conn.execute('ALTER TABLE source_archive_messages DROP CONSTRAINT source_archive_latest_fk')
        conn.execute('DROP TABLE source_archive_revisions')
        conn.execute('DROP TABLE source_archive_messages')
        conn.execute('DROP TABLE source_archive_consent_versions')
        conn.execute('DROP TABLE source_archive_deletions')
        conn.execute('DROP TABLE source_archive_consents')
        conn.execute('DROP TABLE source_archive_scopes')
    assert all(conn.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0] is None for table in _TABLES)
    assert conn.execute('SELECT value FROM legacy_probe').fetchone()[0] == 'synthetic unchanged'
