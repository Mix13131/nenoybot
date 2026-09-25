ALTER TABLE interventions
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_interventions_url_read_status
    ON interventions ((metadata -> 'url_read' ->> 'status'))
    WHERE metadata ? 'url_read';
