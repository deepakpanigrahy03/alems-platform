-- =============================================================================
-- thermal_exposure / e007_plan_params.sql
-- Extension migration: which session plan parameters a recording ran with
-- =============================================================================
--
-- te_session already records profile_id, profile_sha256 and pacing_plugin.
-- A session plan now also stores its parameters (JSON, keys sorted), including
-- tau_s with tau_status and tau_source, so a provisional value can never be
-- read as measured. NULL for recordings made without a session plan.
-- =============================================================================

ALTER TABLE te_session ADD COLUMN pacing_params_json TEXT;
