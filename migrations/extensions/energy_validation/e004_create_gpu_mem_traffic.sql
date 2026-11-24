-- =============================================================================
-- energy_validation / e004_create_gpu_mem_traffic.sql
-- Extension migration: GPU memory traffic samples and per run reader record
-- =============================================================================
--
-- Written at persistence on every execution path from the traffic reader
-- family (alems.readers.traffic). First plugin: pravaha (method
-- gpu_l2_traffic_pravaha_v1, MEASURED): GPU L2 counters, device wide.
-- Raw bytes per sample; background subtraction in analysis (idle baseline in
-- the run record). Missing values NULL, never 0 (MIC-1). Totals include the
-- source columns: never add all_* to copy_* or kernel_*.
-- t_start_ns / t_end_ns: the reader's host clock as reported by the plugin.
--
-- Rule MSC-4: DDL only in this file.
-- =============================================================================

CREATE TABLE IF NOT EXISTS energy_validation_gpu_mem_traffic_runs (
    run_id            INTEGER NOT NULL REFERENCES runs(run_id),
    reader            TEXT    NOT NULL,
    method_id         TEXT    NOT NULL,
    status            TEXT    NOT NULL CHECK(status IN ('measured', 'unavailable', 'failed')),
    reason            TEXT,
    pravaha_version   TEXT,
    driver            TEXT,
    cuda              TEXT,
    interval_ms       INTEGER,
    device            INTEGER,
    sample_count      INTEGER NOT NULL CHECK(sample_count >= 0),
    baseline_json     TEXT,
    quality_json      TEXT,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (run_id, reader)
);

CREATE TABLE IF NOT EXISTS energy_validation_gpu_mem_traffic_samples (
    sample_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  INTEGER NOT NULL REFERENCES runs(run_id),
    reader                  TEXT    NOT NULL,
    t_start_ns              INTEGER,
    t_end_ns                INTEGER,
    interval_ns             INTEGER,
    copy_bytes              INTEGER,
    kernel_read_miss_bytes  INTEGER,
    kernel_write_bytes      INTEGER,
    all_read_miss_bytes     INTEGER,
    all_write_bytes         INTEGER,
    phase                   TEXT,
    measurement_scope       TEXT    NOT NULL DEFAULT 'device_wide',
    quality_flag            TEXT,
    pravaha_version         TEXT
);
CREATE INDEX IF NOT EXISTS idx_ev_traffic_run ON energy_validation_gpu_mem_traffic_samples(run_id);
