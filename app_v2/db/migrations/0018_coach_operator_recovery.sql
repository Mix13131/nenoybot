-- One explicitly requested operator recovery of an unresponded direct request.
-- The original job/Telegram identity and all prior paid-operation rows survive.
-- Audit fields remain after completion/expiry/forget; they contain no body/hash.
ALTER TABLE coach_jobs ADD COLUMN recovery_attempt INTEGER NOT NULL DEFAULT 0
    CHECK (recovery_attempt BETWEEN 0 AND 1);
ALTER TABLE coach_jobs ADD COLUMN recovery_requested_at TIMESTAMPTZ;
ALTER TABLE coach_jobs ADD CONSTRAINT coach_job_recovery_provenance CHECK (
    (recovery_attempt = 0 AND recovery_requested_at IS NULL)
    OR (recovery_attempt = 1 AND recovery_requested_at IS NOT NULL)
);
