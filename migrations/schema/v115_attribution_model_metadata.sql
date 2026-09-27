-- v115_attribution_model_metadata.sql
-- Adds isolation_level and idle_policy to energy_attribution.
-- attribution_model_version already exists (col 33, default 'v1').
-- Rule S: additive only; existing rows get defaults; no data touched.

ALTER TABLE energy_attribution
    ADD COLUMN isolation_level TEXT DEFAULT 'exclusive';

ALTER TABLE energy_attribution
    ADD COLUMN idle_policy TEXT DEFAULT 'legacy_baseline_subtraction';

INSERT INTO schema_version (version, applied_at, description)
VALUES (115, datetime('now'),
    'isolation_level and idle_policy on energy_attribution for attribution model contract');
