-- =============================================================================
-- thermal_exposure / e002_recording_between_runs.sql
-- Extension migration: continuous experiment recording, samples between runs
-- =============================================================================
--
-- An opted-in experiment is recorded continuously from a short pre-window
-- before its first run to the end of its last run (plus an optional
-- cool-down). Samples taken inside a run are stored in the core sample tables
-- under that run's run_id, exactly as before. Samples taken outside every run
-- (pre-window, gaps between runs, cool-down) have no run, so they are stored
-- here, keyed to the recording (te_session). Each sample is stored once.
--
-- No row is added to runs for a recording, so no existing view, query or
-- report sees anything new.
--
-- Between-runs tables mirror the core tables they continue:
--   te_thermal_samples     <- thermal_samples_v2
--   te_cooling_samples     <- cooling_samples
--   te_gpu_temp_samples    <- gpu_samples (temperature only)
--   te_power_rail_samples  <- power_rail_samples (idle power reference)
-- Values that could not be read are NULL with a quality flag, never 0 (MIC-1).
--
-- te_ambient_samples is rebuilt so room temperature can also be stored
-- between runs (run_id becomes nullable). SQLite cannot drop NOT NULL in
-- place; existing rows are copied with an explicit column list (MSC-6).
--
-- Names are logical machine profile names, never hostnames.
-- =============================================================================


-- Recording lifecycle details on the existing session table. te_session.run_id
-- stays NULL for these recordings: the recording has no row in runs.
ALTER TABLE te_session ADD COLUMN pre_window_ns INTEGER;

ALTER TABLE te_session ADD COLUMN workload_start_ns INTEGER;

ALTER TABLE te_session ADD COLUMN workload_end_ns INTEGER;

ALTER TABLE te_session ADD COLUMN cooldown_status TEXT
    CHECK(cooldown_status IS NULL OR cooldown_status IN
          ('not_requested', 'completed', 'timed_out', 'aborted'));


CREATE TABLE IF NOT EXISTS te_thermal_samples (
    sample_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES te_session(session_id),
    zone_id         INTEGER NOT NULL REFERENCES thermal_zones(zone_id),
    timestamp_ns    INTEGER NOT NULL,
    temp_celsius    REAL,
    quality_flag    TEXT    NOT NULL CHECK(quality_flag IN ('VALID', 'OUT_OF_RANGE', 'READ_FAILED', 'MISSING')),
    invalid_reason  TEXT,
    CHECK(quality_flag <> 'VALID' OR temp_celsius IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS te_cooling_samples (
    sample_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES te_session(session_id),
    device_id       INTEGER NOT NULL REFERENCES cooling_devices(device_id),
    timestamp_ns    INTEGER NOT NULL,
    cur_state       INTEGER,
    quality_flag    TEXT    NOT NULL CHECK(quality_flag IN ('VALID', 'OUT_OF_RANGE', 'READ_FAILED', 'MISSING')),
    invalid_reason  TEXT,
    CHECK(quality_flag <> 'VALID' OR cur_state IS NOT NULL)
);

-- GPU temperature only (whole degrees, as gpu_samples.temperature_c).
CREATE TABLE IF NOT EXISTS te_gpu_temp_samples (
    sample_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES te_session(session_id),
    gpu_index       INTEGER NOT NULL,
    timestamp_ns    INTEGER NOT NULL,
    temperature_c   INTEGER,
    source          TEXT    NOT NULL,
    quality_flag    TEXT    NOT NULL CHECK(quality_flag IN ('VALID', 'OUT_OF_RANGE', 'READ_FAILED', 'MISSING')),
    invalid_reason  TEXT,
    CHECK(quality_flag <> 'VALID' OR temperature_c IS NOT NULL)
);

-- rail_id has no foreign key: power_rails exists only on machines whose
-- platform migration creates it.
CREATE TABLE IF NOT EXISTS te_power_rail_samples (
    sample_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES te_session(session_id),
    rail_id         INTEGER NOT NULL,
    timestamp_ns    INTEGER NOT NULL,
    interval_ns     INTEGER,
    power_mw        REAL,
    quality_flag    TEXT    NOT NULL CHECK(quality_flag IN ('VALID', 'OUT_OF_RANGE', 'READ_FAILED', 'MISSING')),
    invalid_reason  TEXT,
    CHECK(quality_flag <> 'VALID' OR power_mw IS NOT NULL)
);


-- te_ambient_samples rebuild: run_id nullable (NULL between runs).
CREATE TABLE te_ambient_samples_new (
    sample_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER REFERENCES runs(run_id),
    session_id      INTEGER REFERENCES te_session(session_id),
    sensor_name     TEXT    NOT NULL,
    timestamp_ns    INTEGER NOT NULL,
    temp_celsius    REAL,
    quality_flag    TEXT    NOT NULL CHECK(quality_flag IN ('VALID', 'OUT_OF_RANGE', 'READ_FAILED', 'MISSING', 'UNAVAILABLE')),
    invalid_reason  TEXT,
    CHECK(quality_flag <> 'VALID' OR temp_celsius IS NOT NULL),
    CHECK(quality_flag <> 'UNAVAILABLE' OR temp_celsius IS NULL),
    CHECK(run_id IS NOT NULL OR session_id IS NOT NULL)
);

INSERT INTO te_ambient_samples_new
    (sample_id, run_id, session_id, sensor_name, timestamp_ns,
     temp_celsius, quality_flag, invalid_reason)
SELECT sample_id, run_id, session_id, sensor_name, timestamp_ns,
       temp_celsius, quality_flag, invalid_reason
FROM te_ambient_samples;

DROP TABLE te_ambient_samples;

ALTER TABLE te_ambient_samples_new RENAME TO te_ambient_samples;


CREATE INDEX IF NOT EXISTS idx_te_ambient_samples_run_ts      ON te_ambient_samples(run_id, timestamp_ns);
CREATE INDEX IF NOT EXISTS idx_te_ambient_samples_session_ts  ON te_ambient_samples(session_id, timestamp_ns);
CREATE INDEX IF NOT EXISTS idx_te_thermal_samples_session_ts  ON te_thermal_samples(session_id, timestamp_ns);
CREATE INDEX IF NOT EXISTS idx_te_cooling_samples_session_ts  ON te_cooling_samples(session_id, timestamp_ns);
CREATE INDEX IF NOT EXISTS idx_te_gpu_temp_samples_session_ts ON te_gpu_temp_samples(session_id, timestamp_ns);
CREATE INDEX IF NOT EXISTS idx_te_power_rail_samples_session_ts ON te_power_rail_samples(session_id, timestamp_ns);


-- Full thermal timeline of a recording: run samples (core table, runs of the
-- recording's experiment inside the recording window) plus between-runs
-- samples. owner = 'run' or 'between'.
CREATE VIEW IF NOT EXISTS te_v_recording_thermal AS
SELECT s.session_id, 'run' AS owner, t.run_id, t.zone_id, t.timestamp_ns,
       t.temp_celsius, t.quality_flag
FROM te_session s
JOIN runs r ON r.exp_id = s.exp_id
JOIN thermal_samples_v2 t ON t.run_id = r.run_id
WHERE t.timestamp_ns >= s.start_ns
  AND (s.end_ns IS NULL OR t.timestamp_ns < s.end_ns)
UNION ALL
SELECT b.session_id, 'between' AS owner, NULL AS run_id, b.zone_id, b.timestamp_ns,
       b.temp_celsius, b.quality_flag
FROM te_thermal_samples b;
