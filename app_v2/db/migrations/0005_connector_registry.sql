-- TASK 35: persisted/versioned Connector Registry.
-- Connector scope binding is stored outside JSON config so a malformed or
-- malicious payload cannot move a connector to another group.

CREATE TABLE IF NOT EXISTS connectors (
    connector_id TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL CHECK (scope_type IN ('group')),
    scope_id TEXT NOT NULL,
    connector_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('sandbox','shadow','live','paused')),
    current_version INTEGER NOT NULL CHECK (current_version >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(scope_type, scope_id)
);

CREATE TABLE IF NOT EXISTS connector_versions (
    connector_id TEXT NOT NULL
        REFERENCES connectors(connector_id)
        ON DELETE CASCADE,
    version INTEGER NOT NULL CHECK (version >= 1),
    config JSONB NOT NULL,
    created_by TEXT NOT NULL DEFAULT 'system',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(connector_id, version)
);

CREATE INDEX IF NOT EXISTS idx_connectors_scope
    ON connectors(scope_type, scope_id);

CREATE INDEX IF NOT EXISTS idx_connector_versions_created_at
    ON connector_versions(connector_id, created_at DESC);
