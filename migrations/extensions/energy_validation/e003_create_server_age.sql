-- =============================================================================
-- energy_validation / e003_create_server_age.sql
-- Extension migration: machine, driver and serving instance age at run start
-- =============================================================================
--
-- One row per run, written at persistence on every execution path.
-- Method: server_age_variables (MEASURED). All ages are at runs.start_time_ns.
-- time_since_driver_load_s is NULL with driver_load_source = 'none' where no
-- trustworthy driver load time exists (GN100). gpu_busy_observed_since_start_s
-- is a lower bound: GPU busy time seen in A-LEMS samples since server start.
-- Missing values are NULL (MIC-1).
--
-- Rule MSC-4: DDL only in this file.
-- =============================================================================

CREATE TABLE IF NOT EXISTS energy_validation_server_age (
    run_id                           INTEGER PRIMARY KEY REFERENCES runs(run_id),
    exp_id                           INTEGER NOT NULL REFERENCES experiments(exp_id),
    run_start_ns                     INTEGER NOT NULL,
    time_since_boot_s                REAL,
    time_since_driver_load_s         REAL,
    driver_load_source               TEXT    NOT NULL,
    time_since_server_start_s        REAL,
    server_instance_id               TEXT,
    gpu_busy_observed_since_start_s  REAL    CHECK(gpu_busy_observed_since_start_s IS NULL OR gpu_busy_observed_since_start_s >= 0),
    gpu_temp_c                       REAL,
    gpu_sm_clock_mhz                 REAL,
    gpu_mem_clock_mhz                REAL,
    method_id                        TEXT    NOT NULL DEFAULT 'server_age_variables',
    created_at                       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ev_server_age_exp ON energy_validation_server_age(exp_id);
