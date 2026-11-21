-- =============================================================================
-- thermal_exposure / e005_sensor_samples_and_catalog.sql
-- Extension migration: per-sensor temperatures between runs on every platform
-- =============================================================================
--
-- te_sensor_samples   readings by sensor catalog id between runs (pre and post
--                     idle windows, gaps, cool-down), written by the recorder
--                     from the platform's thermal reader on every platform
--                     (Linux zones and GPU, x86 hwmon, Apple Silicon die
--                     sensors). Missing is NULL with a quality flag, never 0.
-- te_sensor_catalog   immutable snapshot of each sensor catalog by its hash,
--                     so the meaning of a catalog id in an old run never
--                     changes after a driver or OS update.
-- te_sensor           rebuilt so a machine profile can name a catalog sensor
--                     (source_table 'catalog' with catalog_id); existing rows
--                     are copied with an explicit column list (MSC-6).
-- te_v_sensor_timeline  one time line per catalog id: run samples
--                     (thermal_samples.sensors_json) and between-runs samples
--                     (te_sensor_samples, plus Linux zones and GPU temperature
--                     from te_thermal_samples / te_gpu_temp_samples, which are
--                     stored once and only named by catalog id here).
--                     Quality flags keep each source's own words (MISSING in
--                     the older tables). No gap is filled, no value carried.
-- =============================================================================


CREATE TABLE IF NOT EXISTS te_sensor_samples (
    sample_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES te_session(session_id),
    catalog_id      TEXT    NOT NULL,
    timestamp_ns    INTEGER NOT NULL,
    temp_celsius    REAL,
    quality_flag    TEXT    NOT NULL CHECK(quality_flag IN ('VALID', 'OUT_OF_RANGE', 'READ_FAILED', 'UNAVAILABLE')),
    catalog_sha256  TEXT,
    CHECK(quality_flag <> 'VALID' OR temp_celsius IS NOT NULL),
    CHECK(quality_flag <> 'UNAVAILABLE' OR temp_celsius IS NULL)
);

CREATE INDEX IF NOT EXISTS idx_te_sensor_samples_session ON te_sensor_samples(session_id, catalog_id, timestamp_ns);

CREATE TABLE IF NOT EXISTS te_sensor_catalog (
    catalog_sha256   TEXT PRIMARY KEY,
    schema_version   INTEGER NOT NULL,
    catalog_json     TEXT    NOT NULL,
    first_seen_utc   TEXT    NOT NULL
);


CREATE TABLE te_sensor_new (
    sensor_id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_profile                  TEXT    NOT NULL,
    profile_sha256                   TEXT    NOT NULL,
    sensor_name                      TEXT    NOT NULL,
    source_table                     TEXT    NOT NULL CHECK(source_table IN ('thermal_samples_v2', 'gpu_samples', 'te_ambient_samples', 'catalog')),
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
    interface_resolution_c           REAL    CHECK(interface_resolution_c IS NULL OR interface_resolution_c > 0),
    observed_step_c                  REAL    CHECK(observed_step_c IS NULL OR observed_step_c > 0),
    resolution_status                TEXT    CHECK(resolution_status IS NULL OR resolution_status IN
                                                   ('measured', 'interface_only', 'typed_unverified')),
    catalog_sha256                   TEXT,
    catalog_id                       TEXT,
    UNIQUE(machine_profile, sensor_name),
    CHECK(valid_min_c < valid_max_c),
    CHECK(expected_update_interval_ms IS NULL OR expected_update_interval_source IS NOT NULL),
    CHECK((source_table = 'thermal_samples_v2' AND zone_type IS NOT NULL AND zone_index IS NOT NULL)
       OR (source_table = 'gpu_samples' AND gpu_index IS NOT NULL)
       OR (source_table = 'te_ambient_samples')
       OR (source_table = 'catalog' AND catalog_id IS NOT NULL))
);

INSERT INTO te_sensor_new
    (sensor_id, machine_profile, profile_sha256, sensor_name, source_table, zone_type, zone_index,
     gpu_index, canonical_role, unit, nominal_resolution_c, valid_min_c, valid_max_c,
     expected_update_interval_ms, expected_update_interval_source, is_main, loaded_at_utc,
     interface_resolution_c, observed_step_c, resolution_status, catalog_sha256)
SELECT sensor_id, machine_profile, profile_sha256, sensor_name, source_table, zone_type, zone_index,
     gpu_index, canonical_role, unit, nominal_resolution_c, valid_min_c, valid_max_c,
     expected_update_interval_ms, expected_update_interval_source, is_main, loaded_at_utc,
     interface_resolution_c, observed_step_c, resolution_status, catalog_sha256
FROM te_sensor;

DROP TABLE te_sensor;

ALTER TABLE te_sensor_new RENAME TO te_sensor;


CREATE VIEW IF NOT EXISTS te_v_sensor_timeline AS
SELECT 'run'                                   AS source_phase,
       t.run_id                                AS run_id,
       NULL                                    AS session_id,
       t.catalog_sha256                        AS catalog_sha256,
       j.key                                   AS catalog_id,
       t.timestamp_ns                          AS timestamp_ns,
       j.value                                 AS temp_celsius,
       json_extract(t.sensor_quality_json, '$."' || j.key || '"') AS quality_flag,
       'wall_clock'                            AS clock_basis
FROM thermal_samples t, json_each(t.sensors_json) j
WHERE t.sensors_json IS NOT NULL
UNION ALL
SELECT 'between', NULL, s.session_id, s.catalog_sha256, s.catalog_id, s.timestamp_ns,
       s.temp_celsius, s.quality_flag, 'wall_at_open_plus_monotonic_v1'
FROM te_sensor_samples s
UNION ALL
-- Linux zones between runs are stored once, in te_thermal_samples; named here
-- with the same catalog id the sensor catalog uses
SELECT 'between', NULL, z.session_id, NULL,
       'thermal_zone:' || tz.zone_index || ':' || tz.zone_type, z.timestamp_ns,
       z.temp_celsius, z.quality_flag, 'wall_at_open_plus_monotonic_v1'
FROM te_thermal_samples z JOIN thermal_zones tz ON tz.zone_id = z.zone_id
UNION ALL
SELECT 'between', NULL, g.session_id, NULL,
       'nvml:gpu' || g.gpu_index || ':temperature', g.timestamp_ns,
       g.temperature_c, g.quality_flag, 'wall_at_open_plus_monotonic_v1'
FROM te_gpu_temp_samples g;
