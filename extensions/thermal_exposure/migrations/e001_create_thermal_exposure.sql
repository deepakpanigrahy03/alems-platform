-- =============================================================================
-- thermal_exposure / e001_create_thermal_exposure.sql
-- Extension migration: thermal exposure registers and session tables
-- =============================================================================
--
-- Runs only on machines where thermal_exposure is listed in
-- [extensions] active in config/app_settings.yaml (applied by alems dev migrate).
--
-- Registers (filled when the extension activates, from a machine profile):
--   te_channel   one row per power channel per machine profile
--   te_sensor    one row per temperature sensor per machine profile (stable metadata only)
-- Session tables (filled by the session recorder and checks):
--   te_session, te_idle_window, te_clock_anchor, te_session_check
-- Room temperature samples:
--   te_ambient_samples  (thermal_samples_v2 cannot hold an external probe:
--                        zones absent from sysfs are deactivated at every
--                        harness start, the reader reads sysfs files only,
--                        and temp_celsius is NOT NULL)
--
-- Names are logical machine profile names, never hostnames.
-- Values that could not be measured are NULL, never 0 (MIC-1).
-- Rule MSC-4: DDL only in this file.
-- =============================================================================

CREATE TABLE IF NOT EXISTS te_channel (
    channel_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_profile       TEXT    NOT NULL,
    profile_sha256        TEXT    NOT NULL,
    channel_name          TEXT    NOT NULL,
    source_table          TEXT    NOT NULL CHECK(source_table IN ('power_rail_samples', 'energy_sample_domains', 'gpu_samples')),
    source_key            TEXT    NOT NULL,
    unit                  TEXT    NOT NULL CHECK(unit IN ('mW', 'uJ')),
    meaning               TEXT    NOT NULL,
    measurement_kind      TEXT    NOT NULL CHECK(measurement_kind IN ('power_reading', 'energy_counter', 'interval_energy')),
    channel_role          TEXT    NOT NULL CHECK(channel_role IN ('component', 'aggregate', 'overlapping')),
    parent_channel        TEXT,
    boundary              TEXT,
    attribution_eligible  INTEGER NOT NULL CHECK(attribution_eligible IN (0, 1)),
    is_main               INTEGER NOT NULL DEFAULT 0 CHECK(is_main IN (0, 1)),
    loaded_at_utc         TEXT    NOT NULL,
    UNIQUE(machine_profile, channel_name),
    CHECK(boundary IS NOT NULL OR attribution_eligible = 0),
    CHECK(channel_role <> 'overlapping' OR attribution_eligible = 0)
);

CREATE TABLE IF NOT EXISTS te_sensor (
    sensor_id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_profile                  TEXT    NOT NULL,
    profile_sha256                   TEXT    NOT NULL,
    sensor_name                      TEXT    NOT NULL,
    source_table                     TEXT    NOT NULL CHECK(source_table IN ('thermal_samples_v2', 'gpu_samples', 'te_ambient_samples')),
    zone_type                        TEXT,
    zone_index                       INTEGER,
    gpu_index                        INTEGER,
    canonical_role                   TEXT    NOT NULL,
    unit                             TEXT    NOT NULL CHECK(unit = 'C'),
    nominal_resolution_c             REAL    NOT NULL CHECK(nominal_resolution_c > 0),
    valid_min_c                      REAL    NOT NULL,
    valid_max_c                      REAL    NOT NULL,
    expected_update_interval_ms      REAL    CHECK(expected_update_interval_ms IS NULL OR expected_update_interval_ms > 0),
    expected_update_interval_source  TEXT,
    is_main                          INTEGER NOT NULL DEFAULT 0 CHECK(is_main IN (0, 1)),
    loaded_at_utc                    TEXT    NOT NULL,
    UNIQUE(machine_profile, sensor_name),
    CHECK(valid_min_c < valid_max_c),
    CHECK(expected_update_interval_ms IS NULL OR expected_update_interval_source IS NOT NULL),
    CHECK((source_table = 'thermal_samples_v2' AND zone_type IS NOT NULL AND zone_index IS NOT NULL)
       OR (source_table = 'gpu_samples' AND gpu_index IS NOT NULL)
       OR (source_table = 'te_ambient_samples'))
);

CREATE TABLE IF NOT EXISTS te_session (
    session_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    global_session_id  TEXT    NOT NULL UNIQUE,
    node_name          TEXT    NOT NULL,
    role               TEXT    NOT NULL DEFAULT 'full',
    exp_id             INTEGER,
    run_id             INTEGER REFERENCES runs(run_id),
    profile_id         TEXT    NOT NULL,
    profile_sha256     TEXT    NOT NULL,
    pacing_plugin      TEXT    NOT NULL,
    machine_profile    TEXT    NOT NULL,
    clock_basis        TEXT    NOT NULL,
    status             TEXT    NOT NULL CHECK(status IN ('open', 'closed', 'failed', 'aborted')),
    status_reason      TEXT,
    drift_session      INTEGER CHECK(drift_session IS NULL OR drift_session IN (0, 1)),
    cooling_complete   INTEGER CHECK(cooling_complete IS NULL OR cooling_complete IN (0, 1)),
    ambient_status     TEXT    CHECK(ambient_status IS NULL OR ambient_status IN ('available', 'unavailable')),
    start_ns           INTEGER NOT NULL,
    end_ns             INTEGER,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS te_idle_window (
    idle_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    INTEGER NOT NULL REFERENCES te_session(session_id),
    phase         TEXT    NOT NULL CHECK(phase IN ('pre', 'post')),
    channel_name  TEXT,
    sensor_name   TEXT,
    median_value  REAL,
    mad_value     REAL,
    unit          TEXT,
    sample_count  INTEGER NOT NULL CHECK(sample_count >= 0),
    start_ns      INTEGER NOT NULL,
    end_ns        INTEGER NOT NULL,
    method_id     TEXT    NOT NULL,
    CHECK((channel_name IS NULL) <> (sensor_name IS NULL)),
    CHECK(end_ns >= start_ns)
);

CREATE TABLE IF NOT EXISTS te_clock_anchor (
    anchor_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id            INTEGER NOT NULL REFERENCES te_session(session_id),
    node_name             TEXT    NOT NULL,
    wall_ns               INTEGER NOT NULL,
    mono_ns               INTEGER NOT NULL,
    sync_offset_ns        INTEGER,
    sync_method           TEXT,
    offset_to_control_ns  INTEGER,
    stepped               INTEGER CHECK(stepped IS NULL OR stepped IN (0, 1))
);

CREATE TABLE IF NOT EXISTS te_session_check (
    check_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES te_session(session_id),
    check_name  TEXT    NOT NULL,
    result      TEXT    NOT NULL CHECK(result IN ('pass', 'warn', 'fail')),
    value       REAL,
    threshold   REAL,
    note        TEXT,
    method_id   TEXT,
    UNIQUE(session_id, check_name)
);

CREATE TABLE IF NOT EXISTS te_ambient_samples (
    sample_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES runs(run_id),
    session_id      INTEGER REFERENCES te_session(session_id),
    sensor_name     TEXT    NOT NULL,
    timestamp_ns    INTEGER NOT NULL,
    temp_celsius    REAL,
    quality_flag    TEXT    NOT NULL CHECK(quality_flag IN ('VALID', 'OUT_OF_RANGE', 'READ_FAILED', 'MISSING', 'UNAVAILABLE')),
    invalid_reason  TEXT,
    CHECK(quality_flag <> 'VALID' OR temp_celsius IS NOT NULL),
    CHECK(quality_flag <> 'UNAVAILABLE' OR temp_celsius IS NULL)
);

CREATE INDEX IF NOT EXISTS idx_te_idle_window_session    ON te_idle_window(session_id);
CREATE INDEX IF NOT EXISTS idx_te_clock_anchor_session   ON te_clock_anchor(session_id);
CREATE INDEX IF NOT EXISTS idx_te_ambient_samples_run_ts ON te_ambient_samples(run_id, timestamp_ns);
