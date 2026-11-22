-- One shared monetary ledger for every Naparnik AI purpose. No live defaults.
CREATE TABLE coach_budget_days (
    budget_key TEXT NOT NULL,
    budget_day DATE NOT NULL,
    cap_microusd BIGINT NOT NULL CHECK (cap_microusd > 0),
    reserved_microusd BIGINT NOT NULL DEFAULT 0 CHECK (reserved_microusd >= 0),
    spent_microusd BIGINT NOT NULL DEFAULT 0 CHECK (spent_microusd >= 0),
    blocked BOOLEAN NOT NULL DEFAULT FALSE,
    blocked_reason TEXT,
    PRIMARY KEY (budget_key, budget_day)
);

CREATE TABLE coach_ai_operations (
    operation_id TEXT PRIMARY KEY CHECK (length(operation_id) BETWEEN 1 AND 200),
    budget_key TEXT NOT NULL,
    budget_day DATE NOT NULL,
    purpose TEXT NOT NULL CHECK (length(purpose) BETWEEN 1 AND 80),
    model TEXT NOT NULL CHECK (length(model) BETWEEN 1 AND 160),
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    max_input_tokens INTEGER NOT NULL CHECK (max_input_tokens > 0),
    max_output_tokens INTEGER NOT NULL CHECK (max_output_tokens > 0),
    input_price_microusd_per_million BIGINT NOT NULL CHECK (input_price_microusd_per_million > 0),
    output_price_microusd_per_million BIGINT NOT NULL CHECK (output_price_microusd_per_million > 0),
    reserved_microusd BIGINT NOT NULL CHECK (reserved_microusd > 0),
    charged_microusd BIGINT CHECK (charged_microusd >= 0),
    input_tokens BIGINT CHECK (input_tokens >= 0),
    output_tokens BIGINT CHECK (output_tokens >= 0),
    status TEXT NOT NULL CHECK (status IN ('reserved', 'uncertain', 'settled')),
    outcome TEXT,
    latency_ms INTEGER CHECK (latency_ms >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reconciled_at TIMESTAMPTZ,
    FOREIGN KEY (budget_key, budget_day) REFERENCES coach_budget_days(budget_key, budget_day),
    CHECK ((status = 'settled' AND charged_microusd IS NOT NULL AND input_tokens IS NOT NULL
            AND output_tokens IS NOT NULL AND reconciled_at IS NOT NULL)
        OR (status IN ('reserved', 'uncertain') AND charged_microusd IS NULL AND reconciled_at IS NULL))
);

CREATE INDEX idx_coach_ai_operations_day ON coach_ai_operations (budget_key, budget_day, status);
