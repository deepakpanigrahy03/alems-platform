-- v088_method_registry_doc_anchor.sql
-- Add documentation reference columns to measurement_method_registry.
--
-- measurement_method_registry is the master method definition table.
-- It is the correct owner of documentation location data.
-- After this migration, seed_methodology.py writes doc/section/method_anchor
-- to the registry. Per-run measurement_methodology records reference
-- method_anchor via the method_id foreign key.
--
-- doc          — current filename relative to docs_base (may change on rename)
-- section      — human display text, not used for lookup (may change freely)
-- method_anchor — PERMANENT stable identifier, never changes once assigned
--                 This is the stable key for provenance chain resolution.

ALTER TABLE measurement_method_registry ADD COLUMN doc           TEXT;
ALTER TABLE measurement_method_registry ADD COLUMN section       TEXT;
ALTER TABLE measurement_method_registry ADD COLUMN method_anchor TEXT;

-- Backfill from methodology_docs.yaml values.
-- Covers all 28 methods that were in the original methodology_docs.yaml.
-- Methods added after this migration are populated by seed_methodology.py
-- on next explicit reseed (python3 scripts/seed_methodology.py).

UPDATE measurement_method_registry
SET
    doc           = CASE id
        WHEN 'energy_attribution_v1'            THEN 'energy-attribution.md'
        WHEN 'llm_wait_attribution_v1'          THEN 'llm-wait-energy.md'
        WHEN 'network_wait_energy_v1'           THEN 'network-energy.md'
        WHEN 'llm_energy_sample_v2'             THEN 'energy-attribution.md'
        WHEN 'rapl_msr_pkg_energy'              THEN 'energy-readers.md'
        WHEN 'iokit_power_reader'               THEN 'energy-readers.md'
        WHEN 'iokit_thermal_reader'             THEN 'energy-readers.md'
        WHEN 'ml_energy_estimator'              THEN 'energy-readers.md'
        WHEN 'dummy_energy_reader'              THEN 'energy-readers.md'
        WHEN 'perf_counters'                    THEN 'system-measurement.md'
        WHEN 'thermal_sensor'                   THEN 'system-measurement.md'
        WHEN 'msr_reader'                       THEN 'system-measurement.md'
        WHEN 'turbostat_reader'                 THEN 'system-measurement.md'
        WHEN 'os_scheduler_reader'              THEN 'system-measurement.md'
        WHEN 'os_memory_reader'                 THEN 'system-measurement.md'
        WHEN 'network_measurement'              THEN 'system-measurement.md'
        WHEN 'system_clock'                     THEN 'system-measurement.md'
        WHEN 'ttft_tpot_wall_clock'             THEN 'derived-metrics.md'
        WHEN 'dynamic_energy_calculation'       THEN 'derived-metrics.md'
        WHEN 'ipc_calculation'                  THEN 'derived-metrics.md'
        WHEN 'cache_miss_calculation'           THEN 'derived-metrics.md'
        WHEN 'efficiency_metrics_calculation'   THEN 'derived-metrics.md'
        WHEN 'orchestration_tax_calculation'    THEN 'derived-metrics.md'
        WHEN 'complexity_score_calculation'     THEN 'derived-metrics.md'
        WHEN 'carbon_calculation'               THEN 'derived-metrics.md'
        WHEN 'water_calculation'                THEN 'derived-metrics.md'
        WHEN 'methane_calculation'              THEN 'derived-metrics.md'
        WHEN 'idle_baseline_cpu_pinning_2sigma' THEN 'measurement-methodology.md'
        WHEN 'cpu_fraction_attribution'         THEN 'derived-metrics.md'
        ELSE NULL
    END,
    section       = CASE id
        WHEN 'energy_attribution_v1'            THEN 'Attribution Model v1'
        WHEN 'llm_wait_attribution_v1'          THEN 'LLM Wait Energy Attribution'
        WHEN 'network_wait_energy_v1'           THEN 'Network Wait Energy Attribution'
        WHEN 'llm_energy_sample_v2'             THEN 'LLM Energy Attribution v2 — Sample-Based'
        WHEN 'rapl_msr_pkg_energy'              THEN 'RAPL Package Energy Measurement'
        WHEN 'iokit_power_reader'               THEN 'IOKit Power Reader'
        WHEN 'iokit_thermal_reader'             THEN 'IOKit Thermal Reader'
        WHEN 'ml_energy_estimator'              THEN 'ML Energy Estimator'
        WHEN 'dummy_energy_reader'              THEN 'Dummy Energy Reader'
        WHEN 'perf_counters'                    THEN 'Linux perf Hardware Counters'
        WHEN 'thermal_sensor'                   THEN 'Thermal Zone Sensor'
        WHEN 'msr_reader'                       THEN 'MSR C-State Register Reader'
        WHEN 'turbostat_reader'                 THEN 'Intel Turbostat CPU Frequency Reader'
        WHEN 'os_scheduler_reader'              THEN 'OS Scheduler Statistics Reader'
        WHEN 'os_memory_reader'                 THEN 'OS Memory Statistics Reader'
        WHEN 'network_measurement'              THEN 'Network I/O Measurement'
        WHEN 'system_clock'                     THEN 'System Wall Clock'
        WHEN 'ttft_tpot_wall_clock'             THEN 'TTFT and TPOT Wall Clock Measurement'
        WHEN 'dynamic_energy_calculation'       THEN 'Dynamic Energy Calculation'
        WHEN 'ipc_calculation'                  THEN 'IPC Calculation'
        WHEN 'cache_miss_calculation'           THEN 'Cache Miss Rate Calculation'
        WHEN 'efficiency_metrics_calculation'   THEN 'Energy Efficiency Metrics'
        WHEN 'orchestration_tax_calculation'    THEN 'Orchestration Tax Calculation'
        WHEN 'complexity_score_calculation'     THEN 'Orchestration Complexity Score'
        WHEN 'carbon_calculation'               THEN 'Carbon Emission Calculation'
        WHEN 'water_calculation'                THEN 'Water Consumption Calculation'
        WHEN 'methane_calculation'              THEN 'Methane Emission Calculation'
        WHEN 'idle_baseline_cpu_pinning_2sigma' THEN 'Idle Baseline Methodology'
        WHEN 'cpu_fraction_attribution'         THEN 'CPU Fraction Attribution'
        ELSE NULL
    END,
    method_anchor = CASE id
        WHEN 'energy_attribution_v1'            THEN 'energy-attribution-v1'
        WHEN 'llm_wait_attribution_v1'          THEN 'llm-wait-attribution-v1'
        WHEN 'network_wait_energy_v1'           THEN 'network-wait-energy-v1'
        WHEN 'llm_energy_sample_v2'             THEN 'llm-energy-sample-v2'
        WHEN 'rapl_msr_pkg_energy'              THEN 'rapl-msr-pkg-energy'
        WHEN 'iokit_power_reader'               THEN 'iokit-power-reader'
        WHEN 'iokit_thermal_reader'             THEN 'iokit-thermal-reader'
        WHEN 'ml_energy_estimator'              THEN 'ml-energy-estimator'
        WHEN 'dummy_energy_reader'              THEN 'dummy-energy-reader'
        WHEN 'perf_counters'                    THEN 'perf-counters'
        WHEN 'thermal_sensor'                   THEN 'thermal-sensor'
        WHEN 'msr_reader'                       THEN 'msr-reader'
        WHEN 'turbostat_reader'                 THEN 'turbostat-reader'
        WHEN 'os_scheduler_reader'              THEN 'os-scheduler-reader'
        WHEN 'os_memory_reader'                 THEN 'os-memory-reader'
        WHEN 'network_measurement'              THEN 'network-measurement'
        WHEN 'system_clock'                     THEN 'system-clock'
        WHEN 'ttft_tpot_wall_clock'             THEN 'ttft-tpot-wall-clock'
        WHEN 'dynamic_energy_calculation'       THEN 'dynamic-energy-calculation'
        WHEN 'ipc_calculation'                  THEN 'ipc-calculation'
        WHEN 'cache_miss_calculation'           THEN 'cache-miss-calculation'
        WHEN 'efficiency_metrics_calculation'   THEN 'efficiency-metrics-calculation'
        WHEN 'orchestration_tax_calculation'    THEN 'orchestration-tax-calculation'
        WHEN 'complexity_score_calculation'     THEN 'complexity-score-calculation'
        WHEN 'carbon_calculation'               THEN 'carbon-calculation'
        WHEN 'water_calculation'                THEN 'water-calculation'
        WHEN 'methane_calculation'              THEN 'methane-calculation'
        WHEN 'idle_baseline_cpu_pinning_2sigma' THEN 'idle-baseline-cpu-pinning-2sigma'
        WHEN 'cpu_fraction_attribution'         THEN 'cpu-fraction-attribution'
        ELSE NULL
    END
WHERE id IN (
    'energy_attribution_v1', 'llm_wait_attribution_v1', 'network_wait_energy_v1',
    'llm_energy_sample_v2', 'rapl_msr_pkg_energy', 'iokit_power_reader',
    'iokit_thermal_reader', 'ml_energy_estimator', 'dummy_energy_reader',
    'perf_counters', 'thermal_sensor', 'msr_reader', 'turbostat_reader',
    'os_scheduler_reader', 'os_memory_reader', 'network_measurement', 'system_clock',
    'ttft_tpot_wall_clock', 'dynamic_energy_calculation', 'ipc_calculation',
    'cache_miss_calculation', 'efficiency_metrics_calculation',
    'orchestration_tax_calculation', 'complexity_score_calculation',
    'carbon_calculation', 'water_calculation', 'methane_calculation',
    'idle_baseline_cpu_pinning_2sigma', 'cpu_fraction_attribution'
);
