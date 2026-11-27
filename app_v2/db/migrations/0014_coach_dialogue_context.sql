-- A bot's delivered words are conversation context, never factual evidence.
-- Every retained turn depends on the same participant's current source records.
CREATE TABLE coach_dialogue_turns (
    output_id TEXT PRIMARY KEY REFERENCES coaching_outputs(id) ON DELETE CASCADE,
    chat_id BIGINT NOT NULL,
    message_thread_id BIGINT NOT NULL,
    author_user_id BIGINT NOT NULL,
    telegram_message_id BIGINT NOT NULL CHECK (telegram_message_id > 0),
    text TEXT,
    source_proofs JSONB NOT NULL CHECK (jsonb_typeof(source_proofs)='array'),
    permission_version INTEGER NOT NULL CHECK (permission_version > 0),
    memory_epoch BIGINT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    UNIQUE(chat_id,message_thread_id,telegram_message_id),
    FOREIGN KEY (chat_id,message_thread_id,author_user_id)
        REFERENCES source_archive_consents(chat_id,message_thread_id,author_user_id)
);
CREATE INDEX idx_coach_dialogue_author_recent ON coach_dialogue_turns
    (chat_id,message_thread_id,author_user_id,occurred_at DESC) WHERE text IS NOT NULL;

CREATE FUNCTION coach_dialogue_permission_changed() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NOT NEW.retain_source OR NOT NEW.historical_inference THEN
        UPDATE coach_dialogue_turns SET text=NULL,source_proofs='[]'::jsonb
          WHERE chat_id=NEW.chat_id AND message_thread_id=NEW.message_thread_id
            AND author_user_id=NEW.author_user_id;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER coach_dialogue_permission_invalidation AFTER UPDATE ON source_archive_consents
    FOR EACH ROW EXECUTE FUNCTION coach_dialogue_permission_changed();

CREATE FUNCTION coach_dialogue_memory_changed() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.memory_epoch IS DISTINCT FROM OLD.memory_epoch THEN
        UPDATE coach_dialogue_turns SET text=NULL,source_proofs='[]'::jsonb
          WHERE chat_id=NEW.chat_id AND message_thread_id=NEW.message_thread_id
            AND author_user_id=NEW.author_user_id;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER coach_dialogue_memory_invalidation AFTER UPDATE ON coach_participant_state
    FOR EACH ROW EXECUTE FUNCTION coach_dialogue_memory_changed();

CREATE FUNCTION coach_dialogue_source_changed() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.latest_source_id IS NOT NULL AND NEW.latest_source_id IS DISTINCT FROM OLD.latest_source_id THEN
        UPDATE coach_dialogue_turns SET text=NULL,source_proofs='[]'::jsonb
          WHERE chat_id=OLD.chat_id AND message_thread_id=OLD.message_thread_id
            AND author_user_id=OLD.author_user_id
            AND source_proofs @> jsonb_build_array(jsonb_build_object('source_id',OLD.latest_source_id));
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER coach_dialogue_source_invalidation AFTER UPDATE ON source_archive_messages
    FOR EACH ROW EXECUTE FUNCTION coach_dialogue_source_changed();
