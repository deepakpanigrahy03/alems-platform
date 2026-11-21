-- v124: per-sensor catalog readings in thermal_samples
-- Root cause: inside runs only the reader's legacy readings dict was stored
-- (all_zones_json): platform-specific names, some of them aggregates, and on
-- Apple Silicon a single maximum instead of 24 die sensors. Per-sensor series
-- with stable names are needed on every platform for thermal exposure.
--   sensors_json         {catalog id: degrees C or null}; catalog ids only
--   sensor_quality_json  {catalog id: VALID | OUT_OF_RANGE | READ_FAILED |
--                        UNAVAILABLE} for every sensor expected in the run;
--                        the keys are the run's expected sensor set
--   catalog_sha256       sensor catalog the sample was read against
-- all_zones_json and sensor_count keep their meaning and content.
-- Applies to: ALL platforms. Existing rows keep NULL (no catalog then).

ALTER TABLE thermal_samples ADD COLUMN sensors_json TEXT;

ALTER TABLE thermal_samples ADD COLUMN sensor_quality_json TEXT;

ALTER TABLE thermal_samples ADD COLUMN catalog_sha256 TEXT;
