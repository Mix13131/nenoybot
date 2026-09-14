from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from app_v2.adapters.postgres import connect, get_database_url

MIGRATION_RE = re.compile(r"^(?P<version>\d{4})_(?P<name>.+)\.sql$")
DEFAULT_MIGRATIONS_DIR = Path(__file__).with_name("migrations")


class MigrationError(RuntimeError):
    pass


class MigrationDriftError(MigrationError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    filename: str
    path: Path
    checksum: str
    sql: str


def _checksum(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def discover_migrations(directory: Path | str | None = None) -> list[Migration]:
    root = Path(directory) if directory is not None else DEFAULT_MIGRATIONS_DIR
    migrations: list[Migration] = []

    if not root.exists():
        return migrations

    for path in root.iterdir():
        if not path.is_file():
            continue
        match = MIGRATION_RE.match(path.name)
        if not match:
            continue
        content = path.read_bytes()
        migrations.append(
            Migration(
                version=int(match.group("version")),
                filename=path.name,
                path=path,
                checksum=_checksum(content),
                sql=content.decode("utf-8"),
            )
        )

    migrations.sort(key=lambda item: item.version)
    versions = [item.version for item in migrations]
    if len(versions) != len(set(versions)):
        raise MigrationError("Найдены дублирующиеся версии SQL migrations")
    return migrations


def ensure_migration_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            filename TEXT NOT NULL UNIQUE,
            checksum TEXT NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()


def run_migrations(
    database_url: str | None = None,
    migrations_dir: Path | str | None = None,
) -> list[int]:
    url = get_database_url(database_url)
    migrations = discover_migrations(migrations_dir)
    applied_now: list[int] = []

    with connect(url) as conn:
        ensure_migration_table(conn)

        for migration in migrations:
            row = conn.execute(
                "SELECT filename, checksum FROM schema_migrations WHERE version = %s",
                (migration.version,),
            ).fetchone()

            if row is not None:
                filename, checksum = row
                if filename != migration.filename or checksum != migration.checksum:
                    raise MigrationDriftError(
                        f"Migration {migration.version:04d} уже применена, но файл или checksum изменился"
                    )
                continue

            try:
                with conn.transaction():
                    conn.execute(migration.sql, prepare=False)
                    conn.execute(
                        """
                        INSERT INTO schema_migrations(version, filename, checksum)
                        VALUES (%s, %s, %s)
                        """,
                        (migration.version, migration.filename, migration.checksum),
                    )
            except Exception as exc:
                raise MigrationError(
                    f"Не удалось применить migration {migration.filename}: {exc}"
                ) from exc

            applied_now.append(migration.version)

    return applied_now


def main() -> None:
    applied = run_migrations()
    if applied:
        print("Applied migrations:", ", ".join(f"{v:04d}" for v in applied))
    else:
        print("No pending migrations")


if __name__ == "__main__":
    main()
