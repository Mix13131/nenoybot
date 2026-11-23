-- Only the dedicated coach uses this registry. No real group or personal grant
-- is seeded. Canonical topic 1 is General / the root of a non-forum group.
ALTER TABLE source_archive_scopes DROP CONSTRAINT source_archive_scopes_topic_scope_check;
ALTER TABLE source_archive_scopes ADD CONSTRAINT source_archive_scopes_topic_scope_check
    CHECK (topic_scope IN ('forum_topic','group'));

CREATE TABLE coach_group_memberships (
    chat_id BIGINT PRIMARY KEY CHECK (chat_id < 0),
    active BOOLEAN NOT NULL,
    active_since TIMESTAMPTZ NOT NULL,
    active_update_id BIGINT CHECK (active_update_id >= 0),
    membership_epoch BIGINT NOT NULL DEFAULT 0 CHECK (membership_epoch >= 0),
    changed_at TIMESTAMPTZ NOT NULL,
    membership_update_id BIGINT CHECK (membership_update_id >= 0)
);

-- The originating Telegram order distinguishes a delayed join from an actual
-- boundary crossed by an older queued answer, including control receipts.
ALTER TABLE coaching_outputs ADD COLUMN origin_occurred_at TIMESTAMPTZ;
ALTER TABLE coaching_outputs ADD COLUMN origin_update_id BIGINT CHECK (origin_update_id >= 0);
UPDATE coaching_outputs o SET origin_occurred_at=j.occurred_at,origin_update_id=j.telegram_update_id
    FROM coach_jobs j WHERE o.job_id=j.id;

-- Leaving a group invalidates ALL queued work, including control receipts.
-- Topic enabled flags and individual stop/forget state are preserved on rejoin.
CREATE FUNCTION coach_membership_changed() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF (OLD.active AND NOT NEW.active) OR NEW.membership_epoch <> OLD.membership_epoch THEN
        UPDATE coach_jobs SET status='cancelled',current_text=NULL,source_checksum=NULL,
            source_id=NULL,lease_token=NULL,lease_until=NULL,reason='bot_removed_from_group'
          WHERE chat_id=NEW.chat_id AND status IN ('pending','processing')
            AND (NOT NEW.active OR (occurred_at,telegram_update_id) <= (NEW.active_since,NEW.active_update_id));
        UPDATE coaching_outputs SET status='cancelled',text=NULL,source_proofs='[]'::jsonb,
            request_checksum=NULL,source_id=NULL,lease_token=NULL,lease_until=NULL
          WHERE chat_id=NEW.chat_id AND status IN ('pending','sending')
            AND (NOT NEW.active OR origin_occurred_at IS NULL OR origin_update_id IS NULL
                 OR (origin_occurred_at,origin_update_id) <= (NEW.active_since,NEW.active_update_id));
        UPDATE coach_message_versions SET checksum=NULL WHERE chat_id=NEW.chat_id
            AND (NOT NEW.active OR (revision_at,telegram_update_id) <= (NEW.active_since,NEW.active_update_id));
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER coach_membership_invalidation AFTER UPDATE ON coach_group_memberships
    FOR EACH ROW EXECUTE FUNCTION coach_membership_changed();
