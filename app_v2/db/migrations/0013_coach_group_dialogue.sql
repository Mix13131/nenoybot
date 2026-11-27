-- Explicit group-default policy for the separately configured coach runtime.
-- Existing participant records and personal opt-outs are never upgraded here.
ALTER TABLE coach_participant_state ADD COLUMN memory_epoch BIGINT NOT NULL DEFAULT 0;
ALTER TABLE source_archive_consents ALTER COLUMN actor_user_id DROP NOT NULL;
ALTER TABLE source_archive_consents ADD COLUMN grant_basis TEXT NOT NULL DEFAULT 'self'
    CHECK (grant_basis IN ('self','group_default'));
ALTER TABLE source_archive_consents ADD CONSTRAINT source_archive_grant_provenance
    CHECK ((grant_basis='self' AND actor_user_id IS NOT NULL AND actor_user_id=author_user_id)
        OR (grant_basis='group_default' AND actor_user_id IS NULL));

ALTER TABLE source_archive_consent_versions ADD COLUMN grant_basis TEXT NOT NULL DEFAULT 'self'
    CHECK (grant_basis IN ('self','group_default'));
ALTER TABLE source_archive_consent_versions ADD COLUMN actor_user_id BIGINT;
UPDATE source_archive_consent_versions SET actor_user_id=author_user_id;
ALTER TABLE source_archive_consent_versions ADD CONSTRAINT source_archive_version_provenance
    CHECK ((grant_basis='self' AND actor_user_id IS NOT NULL AND actor_user_id=author_user_id)
        OR (grant_basis='group_default' AND actor_user_id IS NULL));

ALTER TABLE coaching_outputs ADD COLUMN bundle_id TEXT;
ALTER TABLE coaching_outputs ADD COLUMN part_index INTEGER NOT NULL DEFAULT 0 CHECK (part_index>=0);
UPDATE coaching_outputs SET bundle_id=id;
ALTER TABLE coaching_outputs ALTER COLUMN bundle_id SET NOT NULL;
CREATE UNIQUE INDEX idx_coach_output_bundle_part ON coaching_outputs(bundle_id,part_index);

CREATE TABLE coaching_output_experiments (
    output_id TEXT NOT NULL REFERENCES coaching_outputs(id) ON DELETE CASCADE,
    experiment_id TEXT NOT NULL REFERENCES coach_episodes(id),
    PRIMARY KEY (output_id,experiment_id)
);
INSERT INTO coaching_output_experiments(output_id,experiment_id)
    SELECT id,experiment_id FROM coaching_outputs WHERE experiment_id IS NOT NULL;
