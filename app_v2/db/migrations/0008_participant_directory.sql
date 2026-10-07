ALTER TABLE users
    ADD COLUMN IF NOT EXISTS username TEXT,
    ADD COLUMN IF NOT EXISTS identity_observed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS identity_observed_update_id BIGINT;

ALTER TABLE chat_members
    ADD COLUMN IF NOT EXISTS current_username TEXT,
    ADD COLUMN IF NOT EXISTS current_display_name TEXT,
    ADD COLUMN IF NOT EXISTS aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS identity_observed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS identity_observed_update_id BIGINT;

UPDATE chat_members cm
SET current_display_name = COALESCE(cm.current_display_name, u.display_name),
    first_seen_at = COALESCE(cm.first_seen_at, cm.joined_at),
    identity_observed_at = COALESCE(cm.identity_observed_at, cm.last_seen_at, cm.joined_at)
FROM users u
WHERE u.id = cm.user_id
  AND (
      cm.current_display_name IS NULL
      OR cm.first_seen_at IS NULL
      OR cm.identity_observed_at IS NULL
  );

WITH observed_activity AS (
    SELECT user_id, MAX(observed_at) AS latest_observed_at
    FROM (
        SELECT user_id, last_seen_at AS observed_at
        FROM chat_members
        WHERE last_seen_at IS NOT NULL
        UNION ALL
        SELECT user_id, created_at AS observed_at
        FROM messages
        WHERE user_id IS NOT NULL
    ) activity
    GROUP BY user_id
)
UPDATE users u
SET identity_observed_at = COALESCE(
        u.identity_observed_at,
        activity.latest_observed_at,
        u.created_at
    )
FROM observed_activity activity
WHERE u.id = activity.user_id
  AND u.identity_observed_at IS NULL;

UPDATE users
SET identity_observed_at = created_at
WHERE identity_observed_at IS NULL;

ALTER TABLE chat_members ALTER COLUMN first_seen_at SET DEFAULT CURRENT_TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_chat_members_directory_recent
    ON chat_members(chat_id, last_seen_at DESC);
