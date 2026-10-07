ALTER TABLE interventions
    DROP CONSTRAINT IF EXISTS interventions_primary_action_check;

ALTER TABLE interventions
    ADD CONSTRAINT interventions_primary_action_check
    CHECK (primary_action IN ('ignore', 'reply', 'reaction_only', 'act', 'schedule'));

