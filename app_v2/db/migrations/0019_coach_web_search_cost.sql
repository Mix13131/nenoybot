-- Priced external web lookups are reserved before the Responses web_search tool.
-- Existing token-only operations remain valid with zero tool use.
ALTER TABLE coach_ai_operations ADD COLUMN max_tool_cost_microusd BIGINT NOT NULL DEFAULT 0
    CHECK (max_tool_cost_microusd BETWEEN 0 AND 1000000);
ALTER TABLE coach_ai_operations ADD COLUMN tool_calls INTEGER NOT NULL DEFAULT 0
    CHECK (tool_calls BETWEEN 0 AND 100);
ALTER TABLE coach_ai_operations ADD COLUMN tool_cost_microusd BIGINT NOT NULL DEFAULT 0
    CHECK (tool_cost_microusd BETWEEN 0 AND 1000000);
