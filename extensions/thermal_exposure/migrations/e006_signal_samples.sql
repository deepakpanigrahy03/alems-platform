-- =============================================================================
-- thermal_exposure / e006_signal_samples.sql
-- Extension migration: thermal-response signals between runs
-- =============================================================================
--
-- te_signal_samples    raw signal values between runs (pre and post idle
--                      windows, gaps, cool-down), read by the platform thermal
--                      reader (read_signals) and NVML. One typed value column
--                      is filled: integers (bitmasks, counters, levels),
--                      reals, or text (categories, versions, and integers
--                      outside the signed 64-bit range, kept exact). Missing
--                      is all three NULL with its status, never 0.
-- te_signal_catalog    immutable snapshot of each signal catalog by its hash
--                      (source, field, raw unit, kind, decoder, interpretation,
--                      analysis_enabled), so a signal id keeps its meaning.
-- te_v_signal_timeline one time line per signal id: run samples
--                      (thermal_samples.signals_json) and between-runs samples.
--                      No gap is filled and no value is carried forward.
-- =============================================================================


CREATE TABLE IF NOT EXISTS te_signal_samples (
    sample_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id             INTEGER NOT NULL REFERENCES te_session(session_id),
    signal_id              TEXT    NOT NULL,
    timestamp_ns           INTEGER NOT NULL,
    value_int              INTEGER,
    value_real             REAL,
    value_text             TEXT,
    status                 TEXT    NOT NULL CHECK(status IN ('VALID', 'OUT_OF_RANGE', 'READ_FAILED', 'UNAVAILABLE')),
    signal_catalog_sha256  TEXT,
    CHECK((value_int IS NOT NULL) + (value_real IS NOT NULL) + (value_text IS NOT NULL) <= 1),
    CHECK(status <> 'VALID' OR value_int IS NOT NULL OR value_real IS NOT NULL OR value_text IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_te_signal_samples_session ON te_signal_samples(session_id, signal_id, timestamp_ns);

CREATE TABLE IF NOT EXISTS te_signal_catalog (
    signals_sha256   TEXT PRIMARY KEY,
    schema_version   INTEGER NOT NULL,
    signals_json     TEXT    NOT NULL,
    first_seen_utc   TEXT    NOT NULL
);

CREATE VIEW IF NOT EXISTS te_v_signal_timeline AS
SELECT 'run'                                        AS source_phase,
       t.run_id                                     AS run_id,
       NULL                                         AS session_id,
       t.signal_catalog_sha256                      AS signal_catalog_sha256,
       j.key                                        AS signal_id,
       t.timestamp_ns                               AS timestamp_ns,
       json_extract(j.value, '$.value')             AS value,
       json_extract(j.value, '$.status')            AS status,
       'wall_clock'                                 AS clock_basis
FROM thermal_samples t, json_each(t.signals_json) j
WHERE t.signals_json IS NOT NULL
UNION ALL
SELECT 'between', NULL, s.session_id, s.signal_catalog_sha256, s.signal_id, s.timestamp_ns,
       COALESCE(s.value_int, s.value_real, s.value_text), s.status,
       'wall_at_open_plus_monotonic_v1'
FROM te_signal_samples s;
