-- Operator-supplied Telegram Desktop snapshots have no Telegram update ID.
-- Audit stores metadata only; bodies remain in the existing forgettable archive.
CREATE TABLE source_archive_imports (
    id TEXT PRIMARY KEY,
    chat_id BIGINT NOT NULL CHECK (chat_id < 0),
    file_sha256 TEXT NOT NULL CHECK (file_sha256 ~ '^[0-9a-f]{64}$'),
    source_format TEXT NOT NULL CHECK (source_format = 'telegram_desktop_json'),
    parser_version INTEGER NOT NULL CHECK (parser_version > 0),
    imported_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    input_events INTEGER NOT NULL CHECK (input_events >= 0),
    UNIQUE (chat_id, file_sha256)
);

ALTER TABLE source_archive_revisions ALTER COLUMN telegram_update_id DROP NOT NULL;
ALTER TABLE source_archive_revisions ADD COLUMN source_origin TEXT NOT NULL DEFAULT 'telegram_update'
    CHECK (source_origin IN ('telegram_update', 'historical_import'));
ALTER TABLE source_archive_revisions ADD COLUMN import_batch_id TEXT REFERENCES source_archive_imports(id);
ALTER TABLE source_archive_revisions ADD CONSTRAINT source_archive_origin_integrity CHECK (
    (source_origin = 'telegram_update' AND telegram_update_id IS NOT NULL AND import_batch_id IS NULL)
    OR (source_origin = 'historical_import' AND telegram_update_id IS NULL AND import_batch_id IS NOT NULL)
);
CREATE UNIQUE INDEX source_archive_import_revision_identity
    ON source_archive_revisions(chat_id, telegram_message_id, revision_at)
    WHERE source_origin = 'historical_import';
