-- =============================================================================
-- energy_validation / e006_create_kernel_traffic.sql
-- Extension migration: Nsight Compute kernel traffic passes (method
-- kernel_traffic_ncu_v1). A pass is a separate profiling execution, never
-- timed and never used for energy. Kernel rows and the pass row are written in
-- one transaction. Failed passes keep NULL aggregates and a reason.
-- Rule MSC-4: DDL only in this file.
-- =============================================================================

CREATE TABLE IF NOT EXISTS energy_validation_kernel_traffic_passes (
    pass_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    matched_run_id        INTEGER REFERENCES runs(run_id),
    attempt_id            INTEGER REFERENCES energy_validation_seq_attempts(attempt_id),
    config_sha256         TEXT,
    workload_json         TEXT    NOT NULL,
    workload_sha256       TEXT    NOT NULL,
    device                TEXT,
    ncu_path              TEXT,
    ncu_version           TEXT,
    driver_profiling_flag TEXT,
    feasibility           TEXT    NOT NULL CHECK(feasibility IN ('pass', 'fail', 'unavailable')),
    feasibility_reason    TEXT,
    metric_set            TEXT    NOT NULL,
    cache_control         TEXT    NOT NULL,
    replay_mode           TEXT    NOT NULL,
    match_regex           TEXT,
    launch_count          INTEGER,
    status                TEXT    NOT NULL CHECK(status IN ('completed', 'failed')),
    reason                TEXT,
    reason_detail         TEXT,
    kernel_count          INTEGER,
    read_bytes            INTEGER,
    write_bytes           INTEGER,
    ref_read_bytes        INTEGER,
    ref_write_bytes       INTEGER,
    read_ratio            REAL,
    write_ratio           REAL,
    tolerance             REAL,
    agreement_status      TEXT    NOT NULL CHECK(agreement_status IN
                            ('within', 'outside', 'no_reference', 'insufficient_coverage',
                             'scope_mismatch', 'device_mismatch', 'zero_reference', 'not_evaluated')),
    method_id             TEXT    NOT NULL,
    started_utc_ns        INTEGER NOT NULL,
    ended_utc_ns          INTEGER NOT NULL,
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CHECK (status = 'failed' OR (read_bytes IS NOT NULL AND write_bytes IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_ev_ktp_run ON energy_validation_kernel_traffic_passes(matched_run_id);

CREATE TABLE IF NOT EXISTS energy_validation_kernel_traffic_kernels (
    pass_id              INTEGER NOT NULL REFERENCES energy_validation_kernel_traffic_passes(pass_id),
    seq                  INTEGER NOT NULL,
    ncu_id               TEXT,
    kernel_name          TEXT,
    read_sectors         INTEGER NOT NULL,
    write_sectors        INTEGER NOT NULL,
    write_requests       INTEGER,
    device_fill_sectors  INTEGER,
    read_bytes           INTEGER NOT NULL,
    write_bytes          INTEGER NOT NULL,
    PRIMARY KEY (pass_id, seq)
);
