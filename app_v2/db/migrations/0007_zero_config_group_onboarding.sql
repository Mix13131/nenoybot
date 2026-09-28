ALTER TABLE chats
    ADD COLUMN IF NOT EXISTS group_access_blocked BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS group_legacy_reconcile_pending BOOLEAN NOT NULL DEFAULT FALSE;

-- The old schema cannot distinguish default denial from an operator denial.
-- Preserve every historical denial; selected legacy rows require an explicit
-- one-time operator reconciliation. Never infer historical intent.
UPDATE chats
SET group_access_blocked = TRUE,
    group_legacy_reconcile_pending = TRUE
WHERE chat_type = 'group' AND is_whitelisted = FALSE;

-- A rolling deployment briefly has an old web writer that omits whitelist.
-- NULL is an INSERT-only sentinel for an omitted value, filled BEFORE the
-- existing NOT NULL constraint is checked. Explicit FALSE is never overridden.
-- This also lets the previous revision run safely during an application rollback.
ALTER TABLE chats ALTER COLUMN is_whitelisted SET DEFAULT NULL;

CREATE OR REPLACE FUNCTION nenoy_chat_insert_defaults()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.is_whitelisted IS NULL THEN
        NEW.is_whitelisted := (NEW.chat_type = 'group');
        IF NEW.chat_type = 'group' AND NEW.group_profile = '{}'::jsonb THEN
            NEW.group_profile := '{"profile":"friends","unsolicited_enabled":true,"initiative":3,"humor":8,"sarcasm":8,"roast":7,"callback":8,"profanity_level":6,"profanity_frequency":3,"sensitivity":8,"callback_fatigue_minutes":180,"timezone":"Europe/Moscow","silence_wakeup_enabled":true,"silence_wakeup_after_minutes":180,"silence_wakeup_start_hour":10,"silence_wakeup_end_hour":22,"silence_wakeup_daily_limit":1}'::jsonb;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS nenoy_chat_insert_defaults_trigger ON chats;
CREATE TRIGGER nenoy_chat_insert_defaults_trigger
BEFORE INSERT ON chats
FOR EACH ROW EXECUTE FUNCTION nenoy_chat_insert_defaults();
