-- =============================================================================
-- thermal_exposure / e004_sensor_resolution_provenance.sql
-- Extension migration: where each sensor's resolution comes from
-- =============================================================================
--
-- Sensor resolution is no longer typed into machine profiles. It is
-- discovered on the machine by scripts/detect_thermal.py (hw_config.json,
-- key thermal_sensors) and copied into te_sensor when the profile loads:
--
--   interface_resolution_c  granularity the interface format implies
--                           (sysfs millidegrees -> 0.001, NVML -> 1.0)
--   observed_step_c         smallest step seen at least twice by the probe;
--                           NULL when too few changes were seen (never 0)
--   resolution_status       measured          nominal = observed step
--                           interface_only    nominal = interface resolution
--                                             (probe saw too few changes)
--                           typed_unverified  no catalog; value typed in the
--                                             profile, not discovered
--   catalog_sha256          identity of the sensor catalog used
--
-- te_sensor.nominal_resolution_c keeps its meaning: the resolution the
-- analysis rules use. These columns record its origin.
-- =============================================================================


ALTER TABLE te_sensor ADD COLUMN interface_resolution_c REAL
    CHECK(interface_resolution_c IS NULL OR interface_resolution_c > 0);

ALTER TABLE te_sensor ADD COLUMN observed_step_c REAL
    CHECK(observed_step_c IS NULL OR observed_step_c > 0);

ALTER TABLE te_sensor ADD COLUMN resolution_status TEXT
    CHECK(resolution_status IS NULL OR resolution_status IN
          ('measured', 'interface_only', 'typed_unverified'));

ALTER TABLE te_sensor ADD COLUMN catalog_sha256 TEXT;
