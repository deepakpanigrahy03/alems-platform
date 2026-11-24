-- =============================================================================
-- energy_validation / e007_create_nvtx_agreement.sql
-- Extension migration: agreement between NVTX ranges in an Nsight Systems report
-- and the spans A-LEMS stored for the same run (method nvtx_span_agreement_v1).
-- One row per check; written only by scripts/tools/check_nvtx_spans.py --write.
-- Rule MSC-4: DDL only in this file.
-- =============================================================================

CREATE TABLE IF NOT EXISTS energy_validation_nvtx_agreement (
    check_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                 INTEGER NOT NULL REFERENCES runs(run_id),
    nsys_report_path       TEXT    NOT NULL,
    nsys_report_sha256     TEXT,
    nsys_version           TEXT,
    spans_stored           INTEGER NOT NULL,
    ranges_found           INTEGER NOT NULL,
    matched                INTEGER NOT NULL,
    missing                INTEGER NOT NULL,
    outside_tolerance      INTEGER NOT NULL,
    max_start_offset_ms    REAL,
    max_duration_diff_ms   REAL,
    tol_ms                 REAL    NOT NULL,
    status                 TEXT    NOT NULL CHECK(status IN ('agree', 'disagree', 'no_ranges', 'trace_missing')),
    method_id              TEXT    NOT NULL,
    checked_utc_ns         INTEGER NOT NULL,
    created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ev_nvtx_run ON energy_validation_nvtx_agreement(run_id);
