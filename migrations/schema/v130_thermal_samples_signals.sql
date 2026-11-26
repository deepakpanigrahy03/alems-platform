-- v130: thermal-response signals in thermal_samples
-- Root cause: runs stored temperatures only; whether the machine reacted (GPU
-- clock-event reasons and counters, GB10 power-limit level, Intel TCC offset,
-- macOS thermal pressure) was not recorded, so a slow or cool run could not be
-- told apart from a throttled one.
--   signals_json           {signal id: {"value": raw value or null,
--                          "status": VALID | OUT_OF_RANGE | READ_FAILED |
--                          UNAVAILABLE}} for the signals read on that tick.
--                          status says whether the read worked; what a value
--                          means and whether analysis may use it is in the
--                          signal catalog (interpretation, analysis_enabled).
--   signal_catalog_sha256  signal catalog the values were read against
-- Applies to: ALL platforms. Existing rows keep NULL (no signals then).

ALTER TABLE thermal_samples ADD COLUMN signals_json TEXT;

ALTER TABLE thermal_samples ADD COLUMN signal_catalog_sha256 TEXT;
