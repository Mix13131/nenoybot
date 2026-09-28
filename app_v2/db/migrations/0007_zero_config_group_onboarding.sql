ALTER TABLE chats
    ADD COLUMN IF NOT EXISTS group_access_blocked BOOLEAN NOT NULL DEFAULT FALSE;

-- Historical is_whitelisted=false was the pre-D-055 default, not evidence of an
-- explicit operator block. Explicit blocks are recorded in group_access_blocked
-- from this migration forward. Existing rows remain unchanged until their next
-- Telegram ingest, which safely auto-enables groups with blocked=false.
