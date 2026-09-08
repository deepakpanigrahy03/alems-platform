-- v083: Add measurement_source to cpu_samples
-- Root cause: cpu_samples is written by three mechanisms (turbostat
-- continuous rows, ARM PMU summary row, Darwin summary row), soon a
-- fourth (SysfsCPUReader fallback per BUG-08), but no column recorded
-- which one wrote a given row. cpu_idle_states already solved this
-- correctly (measurement_source column) — this brings cpu_samples
-- into alignment.
-- Applies to: ALL platforms

ALTER TABLE cpu_samples ADD COLUMN measurement_source TEXT;

-- Backfill existing rows, keyed by each run's real hardware
-- architecture via hardware_config.cpu_architecture — confirmed
-- values: 'x86_64' (AMD, UBUNTU2505), 'aarch64' (GN100/Grace),
-- 'arm64' (Mac/Apple Silicon). hostname and cpu_vendor were
-- considered and rejected: cpu_vendor is empty on every real
-- machine checked, cpu_model is 'Unknown' on Mac, hostname is
-- fragile to future renames.
UPDATE cpu_samples
SET measurement_source = (
    SELECT CASE
        WHEN hc.cpu_architecture = 'arm64'   THEN 'darwin_kperf'
        WHEN hc.cpu_architecture = 'aarch64' THEN 'arm_pmu'
        ELSE 'turbostat'
    END
    FROM runs r
    JOIN hardware_config hc ON hc.hw_id = r.hw_id
    WHERE r.run_id = cpu_samples.run_id
)
WHERE measurement_source IS NULL;
