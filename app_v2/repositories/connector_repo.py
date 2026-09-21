from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app_v2.domain.connectors import ConnectorConfig
from app_v2.services.connector_codec import (
    decode_connector_payload,
    encode_connector_payload,
)


@dataclass(frozen=True)
class PersistedConnectorRecord:
    connector_id: str
    scope_type: str
    scope_id: str
    connector_type: str
    status: str
    version: int
    config: dict[str, Any]


class ConnectorRegistryCorruptionError(RuntimeError):
    """Persisted connector exists but its current snapshot is unreadable/missing."""


class ConnectorRepository:
    def __init__(self, conn) -> None:
        self.conn = conn

    def get_for_scope(
        self,
        scope_type: str,
        scope_id: str,
    ) -> PersistedConnectorRecord | None:
        row = self.conn.execute(
            """
            SELECT
                c.connector_id,
                c.scope_type,
                c.scope_id,
                c.connector_type,
                c.status,
                c.current_version,
                v.config
            FROM connectors c
            LEFT JOIN connector_versions v
              ON v.connector_id=c.connector_id
             AND v.version=c.current_version
            WHERE c.scope_type=%s AND c.scope_id=%s
            """,
            (scope_type, scope_id),
        ).fetchone()
        if row is None:
            return None
        if row[6] is None:
            raise ConnectorRegistryCorruptionError(
                "persisted connector current snapshot is missing"
            )
        return PersistedConnectorRecord(
            connector_id=str(row[0]),
            scope_type=str(row[1]),
            scope_id=str(row[2]),
            connector_type=str(row[3]),
            status=str(row[4]),
            version=int(row[5]),
            config=dict(row[6] or {}),
        )

    def create(
        self,
        *,
        scope_type: str,
        scope_id: str,
        config: ConnectorConfig,
        created_by: str = "system",
    ) -> bool:
        if scope_type != "group":
            raise ValueError("TASK 35 registry supports group scope only")
        if config.connector_type != "telegram_group":
            raise ValueError("group scope requires telegram_group connector_type")
        if config.version != 1:
            raise ValueError("new connector must start at version 1")
        payload = encode_connector_payload(config)
        decode_connector_payload(
            connector_id=config.connector_id,
            connector_type=config.connector_type,
            status=config.status,
            version=config.version,
            payload=payload,
        )
        with self.conn.transaction():
            inserted = self.conn.execute(
                """
                INSERT INTO connectors(
                    connector_id, scope_type, scope_id, connector_type,
                    status, current_version
                )
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING
                RETURNING connector_id
                """,
                (
                    config.connector_id,
                    scope_type,
                    scope_id,
                    config.connector_type,
                    config.status,
                    config.version,
                ),
            ).fetchone()
            if inserted is None:
                return False

            self.conn.execute(
                """
                INSERT INTO connector_versions(
                    connector_id, version, config, created_by
                )
                VALUES (%s,%s,%s::jsonb,%s)
                """,
                (
                    config.connector_id,
                    config.version,
                    json.dumps(payload, ensure_ascii=False),
                    created_by,
                ),
            )
        return True

    def append_version(
        self,
        *,
        connector_id: str,
        expected_version: int,
        config: ConnectorConfig,
        created_by: str = "system",
    ) -> int:
        if config.connector_id != connector_id:
            raise ValueError("connector_id mismatch")
        if config.version != expected_version + 1:
            raise ValueError("config.version must be expected_version + 1")

        payload = encode_connector_payload(config)
        decode_connector_payload(
            connector_id=config.connector_id,
            connector_type=config.connector_type,
            status=config.status,
            version=config.version,
            payload=payload,
        )
        with self.conn.transaction():
            row = self.conn.execute(
                """
                SELECT current_version, connector_type
                FROM connectors
                WHERE connector_id=%s
                FOR UPDATE
                """,
                (connector_id,),
            ).fetchone()
            if row is None:
                raise KeyError("connector not found")
            current_version = int(row[0])
            if current_version != expected_version:
                raise RuntimeError("connector version conflict")
            if str(row[1]) != config.connector_type:
                raise ValueError("connector_type cannot change")

            next_version = current_version + 1
            self.conn.execute(
                """
                INSERT INTO connector_versions(
                    connector_id, version, config, created_by
                )
                VALUES (%s,%s,%s::jsonb,%s)
                """,
                (
                    connector_id,
                    next_version,
                    json.dumps(payload, ensure_ascii=False),
                    created_by,
                ),
            )
            self.conn.execute(
                """
                UPDATE connectors
                SET current_version=%s,
                    status=%s,
                    updated_at=CURRENT_TIMESTAMP
                WHERE connector_id=%s
                """,
                (next_version, config.status, connector_id),
            )
        return next_version

    def delete_for_scope(self, scope_type: str, scope_id: str) -> bool:
        row = self.conn.execute(
            """
            DELETE FROM connectors
            WHERE scope_type=%s AND scope_id=%s
            RETURNING connector_id
            """,
            (scope_type, scope_id),
        ).fetchone()
        self.conn.commit()
        return row is not None
