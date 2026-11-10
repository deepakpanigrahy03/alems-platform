-- =============================================================================
-- energy_validation / e001_create_dcgm_coverage.sql
-- Extension migration: per run DCGM coverage record
-- =============================================================================
--
-- Runs only on machines where energy_validation is listed in
-- [extensions] active in config/app_settings.yaml (applied by alems dev migrate).
--
-- One row per run that has GPU energy samples. Written at persistence time on
-- every execution path (save_pair, save_single, execute_goal).
-- Method: dcgm_shared_window_coverage_v1 (CALCULATED).
--
-- Timestamps: *_utc_ns are wall clock epoch ns (same clock as gpu_samples);
-- *_mono_ns are the monotonic clock of the sampling process (alignment only).
-- shared_window_coverage is NULL, never 0, when it cannot be computed; the
-- reason is in coverage_null_reason (MIC-1).
--
-- Rule MSC-4: DDL only in this file.
-- =============================================================================

CREATE TABLE IF NOT EXISTS energy_validation_dcgm_coverage (
    run_id                  INTEGER PRIMARY KEY REFERENCES runs(run_id),
    source                  TEXT    NOT NULL,
    ready_status            TEXT    NOT NULL CHECK(ready_status IN ('ready', 'timeout', 'unavailable')),
    ready_wait_ms           REAL,
    sampler_start_utc_ns    INTEGER,
    first_valid_utc_ns      INTEGER,
    last_valid_utc_ns       INTEGER,
    first_valid_mono_ns     INTEGER,
    last_valid_mono_ns      INTEGER,
    row_count               INTEGER NOT NULL CHECK(row_count >= 0),
    interior_gaps_gt_300ms  INTEGER CHECK(interior_gaps_gt_300ms IS NULL OR interior_gaps_gt_300ms >= 0),
    max_gap_ms              REAL,
    shared_window_coverage  REAL    CHECK(shared_window_coverage IS NULL OR shared_window_coverage BETWEEN 0 AND 1),
    coverage_null_reason    TEXT,
    cap_detected            INTEGER NOT NULL DEFAULT 0 CHECK(cap_detected IN (0, 1)),
    method_id               TEXT    NOT NULL DEFAULT 'dcgm_shared_window_coverage_v1',
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
