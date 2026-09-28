ALTER TABLE chats
    ADD COLUMN IF NOT EXISTS group_access_blocked BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS group_legacy_reconcile_pending BOOLEAN NOT NULL DEFAULT FALSE;

-- The old schema cannot distinguish default denial from an operator denial.
-- Preserve every historical denial. Only an explicitly authorized, exact-match
-- one-time reconciliation can enable a selected legacy row. Do not infer intent
-- from an empty profile, timestamps, or absence of conversation history.
UPDATE chats
SET group_access_blocked = TRUE,
    group_legacy_reconcile_pending = TRUE
WHERE chat_type = 'group' AND is_whitelisted = FALSE;
