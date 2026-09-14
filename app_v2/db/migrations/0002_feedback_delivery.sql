ALTER TABLE outbox
    ADD COLUMN IF NOT EXISTS telegram_message_id BIGINT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_outbox_telegram_message
    ON outbox(destination_id, telegram_message_id)
    WHERE telegram_message_id IS NOT NULL;

ALTER TABLE feedback_events
    ADD COLUMN IF NOT EXISTS feedback_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_events_feedback_id
    ON feedback_events(feedback_id)
    WHERE feedback_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_feedback_scope_created
    ON feedback_events(scope_id, created_at DESC);
