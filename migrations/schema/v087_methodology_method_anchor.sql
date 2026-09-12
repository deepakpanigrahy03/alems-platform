-- v087_methodology_method_anchor.sql
-- Add stable method_anchor identifier to measurement_methodology.
--
-- method_anchor is a permanent machine identifier that does not change
-- when documentation files are renamed or headings are edited.
-- It is the stable key for provenance chain resolution.
--
-- Backfill mapping: old doc filename + section → method_anchor
-- Rows with unrecognised doc/section combinations get NULL method_anchor.
-- Paper export tools check method_anchor first; fall back to doc+section
-- for historical rows where backfill was not possible.

ALTER TABLE measurement_methodology ADD COLUMN method_anchor TEXT;

-- Backfill from old doc filenames and section headings.
-- Each UPDATE covers one old doc file.
-- method_id is not stored in measurement_methodology, so we match on
-- the method_id stored in the adjacent measurement_method_registry table
-- via the method_id column in measurement_methodology.

UPDATE measurement_methodology
SET method_anchor = CASE method_id

    -- Four-Axis Energy Attribution Framework
    WHEN 'energy_attribution_v1'           THEN 'energy-attribution-v1'
    WHEN 'llm_wait_attribution_v1'         THEN 'llm-wait-attribution-v1'
    WHEN 'network_wait_energy_v1'          THEN 'network-wait-energy-v1'
    WHEN 'llm_energy_sample_v2'            THEN 'llm-energy-sample-v2'

    -- Energy Readers
    WHEN 'rapl_msr_pkg_energy'             THEN 'rapl-msr-pkg-energy'
    WHEN 'iokit_power_reader'              THEN 'iokit-power-reader'
    WHEN 'iokit_thermal_reader'            THEN 'iokit-thermal-reader'
    WHEN 'ml_energy_estimator'             THEN 'ml-energy-estimator'
    WHEN 'dummy_energy_reader'             THEN 'dummy-energy-reader'

    -- System Measurement
    WHEN 'perf_counters'                   THEN 'perf-counters'
    WHEN 'thermal_sensor'                  THEN 'thermal-sensor'
    WHEN 'msr_reader'                      THEN 'msr-reader'
    WHEN 'turbostat_reader'               THEN 'turbostat-reader'
    WHEN 'os_scheduler_reader'             THEN 'os-scheduler-reader'
    WHEN 'os_memory_reader'                THEN 'os-memory-reader'
    WHEN 'network_measurement'             THEN 'network-measurement'
    WHEN 'system_clock'                    THEN 'system-clock'

    -- Derived Metrics
    WHEN 'ttft_tpot_wall_clock'            THEN 'ttft-tpot-wall-clock'
    WHEN 'dynamic_energy_calculation'      THEN 'dynamic-energy-calculation'
    WHEN 'ipc_calculation'                 THEN 'ipc-calculation'
    WHEN 'cache_miss_calculation'          THEN 'cache-miss-calculation'
    WHEN 'efficiency_metrics_calculation'  THEN 'efficiency-metrics-calculation'
    WHEN 'orchestration_tax_calculation'   THEN 'orchestration-tax-calculation'
    WHEN 'complexity_score_calculation'    THEN 'complexity-score-calculation'
    WHEN 'carbon_calculation'              THEN 'carbon-calculation'
    WHEN 'water_calculation'               THEN 'water-calculation'
    WHEN 'methane_calculation'             THEN 'methane-calculation'

    -- Baseline
    WHEN 'idle_baseline_cpu_pinning_2sigma' THEN 'idle-baseline-cpu-pinning-2sigma'
    WHEN 'cpu_fraction_attribution'         THEN 'cpu-fraction-attribution'

    ELSE NULL
END
WHERE method_id IS NOT NULL;
