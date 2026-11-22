-- Stage A. Dormant until explicit runtime activation AND exact topic/person grants.
-- No legacy data is copied, no generic retention policy is changed.
CREATE TABLE source_archive_scopes (
    chat_id BIGINT NOT NULL CHECK (chat_id < 0),
    message_thread_id BIGINT NOT NULL CHECK (message_thread_id > 0),
    topic_scope TEXT NOT NULL DEFAULT 'forum_topic' CHECK (topic_scope = 'forum_topic'),
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    activated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (chat_id, message_thread_id)
);

CREATE TABLE source_archive_consents (
    chat_id BIGINT NOT NULL,
    message_thread_id BIGINT NOT NULL,
    author_user_id BIGINT NOT NULL CHECK (author_user_id > 0),
    version INTEGER NOT NULL CHECK (version > 0),
    retain_source BOOLEAN NOT NULL DEFAULT FALSE,
    historical_inference BOOLEAN NOT NULL DEFAULT FALSE,
    auto_report_analysis BOOLEAN NOT NULL DEFAULT FALSE,
    unsolicited_group_reply BOOLEAN NOT NULL DEFAULT FALSE,
    direct_request_group_reply BOOLEAN NOT NULL DEFAULT FALSE,
    retain_since TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL,
    actor_user_id BIGINT NOT NULL CHECK (actor_user_id = author_user_id),
    PRIMARY KEY (chat_id, message_thread_id, author_user_id),
    FOREIGN KEY (chat_id, message_thread_id)
        REFERENCES source_archive_scopes(chat_id, message_thread_id),
    CHECK (NOT retain_source OR retain_since IS NOT NULL)
);

CREATE TABLE source_archive_consent_versions (
    chat_id BIGINT NOT NULL,
    message_thread_id BIGINT NOT NULL,
    author_user_id BIGINT NOT NULL,
    version INTEGER NOT NULL,
    retain_source BOOLEAN NOT NULL,
    historical_inference BOOLEAN NOT NULL,
    auto_report_analysis BOOLEAN NOT NULL,
    unsolicited_group_reply BOOLEAN NOT NULL,
    direct_request_group_reply BOOLEAN NOT NULL,
    retain_since TIMESTAMPTZ,
    recorded_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (chat_id, message_thread_id, author_user_id, version),
    FOREIGN KEY (chat_id, message_thread_id, author_user_id)
        REFERENCES source_archive_consents(chat_id, message_thread_id, author_user_id)
);

-- Preserve a participant's deletion request even when the delayed source has
-- never been received. No source body or inferred author identity is stored.
CREATE TABLE source_archive_deletions (
    chat_id BIGINT NOT NULL,
    message_thread_id BIGINT NOT NULL,
    author_user_id BIGINT NOT NULL,
    telegram_message_id BIGINT NOT NULL CHECK (telegram_message_id > 0),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (chat_id, message_thread_id, author_user_id, telegram_message_id),
    FOREIGN KEY (chat_id, message_thread_id, author_user_id)
        REFERENCES source_archive_consents(chat_id, message_thread_id, author_user_id)
);

CREATE TABLE source_archive_messages (
    chat_id BIGINT NOT NULL,
    message_thread_id BIGINT NOT NULL,
    author_user_id BIGINT NOT NULL,
    telegram_message_id BIGINT NOT NULL CHECK (telegram_message_id > 0),
    occurred_at TIMESTAMPTZ NOT NULL,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    latest_source_id TEXT,
    PRIMARY KEY (chat_id, telegram_message_id),
    UNIQUE (chat_id, message_thread_id, author_user_id, telegram_message_id),
    FOREIGN KEY (chat_id, message_thread_id, author_user_id)
        REFERENCES source_archive_consents(chat_id, message_thread_id, author_user_id)
);

CREATE TABLE source_archive_revisions (
    source_id TEXT PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    message_thread_id BIGINT NOT NULL,
    author_user_id BIGINT NOT NULL,
    telegram_message_id BIGINT NOT NULL,
    telegram_update_id BIGINT NOT NULL UNIQUE CHECK (telegram_update_id >= 0),
    message_revision INTEGER NOT NULL CHECK (message_revision > 0),
    occurred_at TIMESTAMPTZ NOT NULL,
    revision_at TIMESTAMPTZ NOT NULL CHECK (revision_at >= occurred_at),
    captured_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_edited BOOLEAN NOT NULL,
    reply_to_message_id BIGINT,
    content_kind TEXT NOT NULL,
    available_text TEXT,
    body_coverage TEXT NOT NULL CHECK (body_coverage IN ('full', 'partial', 'unavailable')),
    media_reference JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(media_reference) = 'object'),
    contains_external_content BOOLEAN NOT NULL DEFAULT FALSE,
    consent_version INTEGER NOT NULL,
    checksum TEXT CHECK (checksum ~ '^[0-9a-f]{64}$'),
    source_status TEXT NOT NULL DEFAULT 'active' CHECK (source_status IN ('active', 'deleted')),
    is_deleted BOOLEAN GENERATED ALWAYS AS (source_status = 'deleted') STORED,
    deleted_at TIMESTAMPTZ,
    UNIQUE (chat_id, telegram_message_id, message_revision),
    FOREIGN KEY (chat_id, message_thread_id, author_user_id, telegram_message_id)
        REFERENCES source_archive_messages(chat_id, message_thread_id, author_user_id, telegram_message_id),
    FOREIGN KEY (chat_id, message_thread_id, author_user_id, consent_version)
        REFERENCES source_archive_consent_versions(chat_id, message_thread_id, author_user_id, version),
    CHECK ((source_status = 'active' AND checksum IS NOT NULL AND deleted_at IS NULL)
        OR (source_status = 'deleted' AND available_text IS NULL AND media_reference = '{}'::jsonb
            AND checksum IS NULL AND deleted_at IS NOT NULL))
);

ALTER TABLE source_archive_messages ADD CONSTRAINT source_archive_latest_fk
    FOREIGN KEY (latest_source_id) REFERENCES source_archive_revisions(source_id);

CREATE INDEX idx_source_archive_scope_date ON source_archive_revisions
    (chat_id, message_thread_id, author_user_id, occurred_at, source_id)
    WHERE source_status = 'active';
CREATE INDEX idx_source_archive_scope_latest ON source_archive_revisions
    (chat_id, message_thread_id, author_user_id, occurred_at, telegram_message_id)
    WHERE source_status = 'active';
CREATE INDEX idx_source_archive_message_versions ON source_archive_revisions
    (chat_id, telegram_message_id, revision_at DESC, telegram_update_id DESC);
CREATE INDEX idx_source_archive_message_scope ON source_archive_messages
    (chat_id, message_thread_id, author_user_id, occurred_at);
