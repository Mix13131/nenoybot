-- Dedicated coach runtime only. No grants, real scopes, or outbound messages are seeded.
CREATE TABLE coach_runtime_identity (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    bot_user_id BIGINT NOT NULL CHECK (bot_user_id > 0),
    bound_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE coach_participant_state (
    chat_id BIGINT NOT NULL,
    message_thread_id BIGINT NOT NULL,
    author_user_id BIGINT NOT NULL,
    track_status TEXT NOT NULL DEFAULT 'off' CHECK (track_status IN ('off','active','paused')),
    disclosure_version INTEGER NOT NULL DEFAULT 0,
    disclosure_delivered_at TIMESTAMPTZ,
    last_control_occurred_at TIMESTAMPTZ,
    last_control_update_id BIGINT,
    PRIMARY KEY (chat_id,message_thread_id,author_user_id),
    FOREIGN KEY (chat_id,message_thread_id,author_user_id)
        REFERENCES source_archive_consents(chat_id,message_thread_id,author_user_id)
);

CREATE TABLE coach_ingest_receipts (
    telegram_update_id BIGINT PRIMARY KEY CHECK (telegram_update_id >= 0),
    chat_id BIGINT NOT NULL,
    message_thread_id BIGINT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE coach_message_versions (
    chat_id BIGINT NOT NULL,
    message_thread_id BIGINT NOT NULL,
    author_user_id BIGINT NOT NULL,
    telegram_message_id BIGINT NOT NULL,
    telegram_update_id BIGINT NOT NULL,
    revision_at TIMESTAMPTZ NOT NULL,
    checksum TEXT,
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (chat_id,message_thread_id,author_user_id,telegram_message_id)
);

CREATE TABLE coach_jobs (
    id TEXT PRIMARY KEY,
    telegram_update_id BIGINT NOT NULL UNIQUE,
    chat_id BIGINT NOT NULL,
    message_thread_id BIGINT NOT NULL,
    author_user_id BIGINT NOT NULL,
    telegram_message_id BIGINT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('direct','auto_report','weekly_review','track','index')),
    current_text TEXT,
    source_checksum TEXT,
    source_id TEXT,
    consent_version INTEGER NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    window_days INTEGER NOT NULL DEFAULT 7 CHECK (window_days BETWEEN 1 AND 3660),
    public_confirmed BOOLEAN NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','completed','cancelled','failed','expired')),
    lease_token TEXT,
    lease_until TIMESTAMPTZ,
    attempts INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (chat_id,message_thread_id,author_user_id)
        REFERENCES source_archive_consents(chat_id,message_thread_id,author_user_id)
);
CREATE INDEX idx_coach_job_ready ON coach_jobs(chat_id,message_thread_id,status,mode,created_at);

CREATE TABLE coaching_outputs (
    id TEXT PRIMARY KEY,
    dedupe_key TEXT NOT NULL UNIQUE,
    job_id TEXT REFERENCES coach_jobs(id),
    chat_id BIGINT NOT NULL,
    message_thread_id BIGINT NOT NULL,
    author_user_id BIGINT NOT NULL,
    request_message_id BIGINT NOT NULL,
    request_checksum TEXT,
    source_id TEXT,
    consent_version INTEGER,
    mode TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT,
    source_proofs JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(source_proofs)='array'),
    disclosure BOOLEAN NOT NULL DEFAULT FALSE,
    experiment_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sending','sent','cancelled','uncertain','expired')),
    lease_token TEXT,
    lease_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ NOT NULL,
    telegram_message_id BIGINT,
    FOREIGN KEY (chat_id,message_thread_id,author_user_id)
        REFERENCES source_archive_consents(chat_id,message_thread_id,author_user_id)
);
CREATE INDEX idx_coach_output_ready ON coaching_outputs(chat_id,message_thread_id,status,created_at);

CREATE TABLE coach_episodes (
    id TEXT PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    message_thread_id BIGINT NOT NULL,
    author_user_id BIGINT NOT NULL,
    episode_type TEXT NOT NULL,
    summary TEXT,
    source_proofs JSONB NOT NULL CHECK (jsonb_typeof(source_proofs)='array'),
    dedupe_key TEXT NOT NULL,
    status TEXT CHECK (status IS NULL OR status IN ('prepared','offered','accepted','declined','tried_self_reported','not_tried_self_reported','retired')),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    observed_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (chat_id,message_thread_id,author_user_id,dedupe_key),
    FOREIGN KEY (chat_id,message_thread_id,author_user_id)
        REFERENCES source_archive_consents(chat_id,message_thread_id,author_user_id)
);
ALTER TABLE coaching_outputs ADD CONSTRAINT coaching_output_experiment_fk
    FOREIGN KEY (experiment_id) REFERENCES coach_episodes(id);

-- Disabling one topic cancels its live work, so re-enabling never replays a
-- response prepared before shutdown. Self-service receipts remain deliverable.
CREATE FUNCTION coach_scope_changed() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.enabled AND NOT NEW.enabled THEN
        UPDATE coach_jobs SET status='cancelled',current_text=NULL,source_checksum=NULL,
            source_id=NULL,lease_token=NULL,lease_until=NULL,reason='scope_disabled'
          WHERE chat_id=NEW.chat_id AND message_thread_id=NEW.message_thread_id
            AND status IN ('pending','processing');
        UPDATE coaching_outputs SET status='cancelled',text=NULL,source_proofs='[]'::jsonb,
            request_checksum=NULL,source_id=NULL,lease_token=NULL,lease_until=NULL
          WHERE chat_id=NEW.chat_id AND message_thread_id=NEW.message_thread_id
            AND mode<>'control' AND status IN ('pending','sending');
        UPDATE coach_message_versions SET checksum=NULL
          WHERE chat_id=NEW.chat_id AND message_thread_id=NEW.message_thread_id;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER coach_scope_invalidation AFTER UPDATE ON source_archive_scopes
    FOR EACH ROW EXECUTE FUNCTION coach_scope_changed();

-- A revoke performed through the existing Stage A API must also scrub coach
-- payloads. Version changes invalidate queued replies even when a different
-- permission is changed. No already-sent Telegram message is claimed erased.
CREATE FUNCTION coach_consent_changed() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.version IS DISTINCT FROM OLD.version THEN
        UPDATE coach_jobs SET status='cancelled',current_text=NULL,source_checksum=NULL,
            source_id=NULL,lease_token=NULL,lease_until=NULL,reason='consent_changed'
          WHERE chat_id=NEW.chat_id AND message_thread_id=NEW.message_thread_id
            AND author_user_id=NEW.author_user_id AND status IN ('pending','processing');
        UPDATE coaching_outputs SET status='cancelled',text=NULL,source_proofs='[]'::jsonb,
            request_checksum=NULL,source_id=NULL,lease_token=NULL,lease_until=NULL
          WHERE chat_id=NEW.chat_id AND message_thread_id=NEW.message_thread_id
            AND author_user_id=NEW.author_user_id AND mode<>'control' AND status IN ('pending','sending');
        IF NOT NEW.retain_source OR NOT NEW.historical_inference THEN
            UPDATE coach_episodes SET summary=NULL,source_proofs='[]'::jsonb,is_active=FALSE
              WHERE chat_id=NEW.chat_id AND message_thread_id=NEW.message_thread_id
                AND author_user_id=NEW.author_user_id;
            UPDATE coach_participant_state SET track_status='paused'
              WHERE chat_id=NEW.chat_id AND message_thread_id=NEW.message_thread_id
                AND author_user_id=NEW.author_user_id AND track_status='active';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER coach_consent_invalidation AFTER UPDATE ON source_archive_consents
    FOR EACH ROW EXECUTE FUNCTION coach_consent_changed();

CREATE FUNCTION coach_source_changed() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.latest_source_id IS NOT NULL AND NEW.latest_source_id IS DISTINCT FROM OLD.latest_source_id THEN
        UPDATE coach_jobs SET status='cancelled',current_text=NULL,source_checksum=NULL,
            source_id=NULL,lease_token=NULL,lease_until=NULL,reason='source_changed'
          WHERE chat_id=OLD.chat_id AND message_thread_id=OLD.message_thread_id
            AND author_user_id=OLD.author_user_id AND telegram_message_id=OLD.telegram_message_id
            AND status IN ('pending','processing');
        UPDATE coaching_outputs SET status='cancelled',text=NULL,source_proofs='[]'::jsonb,
            request_checksum=NULL,source_id=NULL,lease_token=NULL,lease_until=NULL
          WHERE chat_id=OLD.chat_id AND message_thread_id=OLD.message_thread_id
            AND author_user_id=OLD.author_user_id AND mode<>'control' AND status IN ('pending','sending')
            AND (request_message_id=OLD.telegram_message_id OR source_proofs @>
                 jsonb_build_array(jsonb_build_object('source_id',OLD.latest_source_id)));
        UPDATE coach_episodes SET summary=NULL,source_proofs='[]'::jsonb,is_active=FALSE
          WHERE chat_id=OLD.chat_id AND message_thread_id=OLD.message_thread_id
            AND author_user_id=OLD.author_user_id
            AND source_proofs @> jsonb_build_array(jsonb_build_object('source_id',OLD.latest_source_id));
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER coach_source_invalidation AFTER UPDATE ON source_archive_messages
    FOR EACH ROW EXECUTE FUNCTION coach_source_changed();
