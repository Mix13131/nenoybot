-- Explicit observation mode records every attempt without a spending cap.
-- Existing rows remain capped; no operator policy or balance is reset.
ALTER TABLE coach_budget_days ALTER COLUMN cap_microusd DROP NOT NULL;
ALTER TABLE coach_budget_days ADD COLUMN budget_mode TEXT NOT NULL DEFAULT 'capped';
ALTER TABLE coach_budget_days ADD CONSTRAINT coach_budget_mode_cap_check
    CHECK ((budget_mode='capped' AND cap_microusd IS NOT NULL AND cap_microusd>0)
        OR (budget_mode='observe' AND cap_microusd IS NULL));
