-- v086: Backfill measurement_source in cpu_samples for existing rows
-- v083 added the column; this migration backfills it using cpu_architecture
-- from hardware_config. Separated from v083 because v083 was already applied
-- without this backfill. Safe to run multiple times (WHERE IS NULL guard).
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
