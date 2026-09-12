# Data Dictionary

Auto-generated from the live database schema on 2026-09-12. Run `bash scripts/build-docs.sh` to regenerate after any schema migration.

**71 tables · 30 views · 1092 columns**

Column provenance is sourced from `core/utils/provenance.py` (`COLUMN_PROVENANCE`) and `measurement_method_registry`. Columns without a provenance entry are marked as unregistered.

---

## Tables

| Table | Rows | Description |
|---|---|---|
| [`analysis_domain_config`](#analysis_domain_config) | 10 | Analysis domain definitions for ETL and reporting |
| [`analysis_view_config`](#analysis_view_config) | 8 | View configuration for the clean/measured view system |
| [`audit_log`](#audit_log) | 0 | Run-level audit events — tracks metric updates and data quality changes |
| [`component_registry`](#component_registry) | 0 | Registered system components with schema and data shape definitions |
| [`cooling_devices`](#cooling_devices) | 27 | Cooling device inventory per platform (fans, liquid coolers) |
| [`cooling_samples`](#cooling_samples) | 40,284 | Cooling device state and target temperature at 1Hz |
| [`cpu_idle_states`](#cpu_idle_states) | 5,968 | CPU C-state residency data per platform |
| [`cpu_samples`](#cpu_samples) | 276 | CPU frequency, IPC, cache counters at 10Hz |
| [`device_telemetry`](#device_telemetry) | 501 | Generic device telemetry samples for non-standard hardware |
| [`energy_attribution`](#energy_attribution) | 1,546 | Phase-attributed energy values per run (planning, tool, synthesis) |
| [`energy_derived_metrics`](#energy_derived_metrics) | 8,320 | Computed energy metrics derived from raw samples via ETL |
| [`energy_domains`](#energy_domains) | 29 | Energy domain taxonomy (pkg, core, uncore, dram, gpu) |
| [`energy_sample_domains`](#energy_sample_domains) | 3,206,530 | Domain assignments for energy samples (pkg, core, uncore, dram, gpu) |
| [`energy_samples`](#energy_samples) | 0 | Raw RAPL/SPBM/IOKit counter reads at 100Hz |
| [`energy_samples_v2`](#energy_samples_v2) | 336,217 | Energy samples schema v2 with domain registry integration |
| [`energy_sources`](#energy_sources) | 9 | Energy source registry (rapl, spbm, iokit, dcgm, arm_pmu) |
| [`environment_config`](#environment_config) | 67 | Software environment fingerprint — git commit, package versions |
| [`etl_queue`](#etl_queue) | 672 | Pending and completed ETL job tracking |
| [`eval_criteria`](#eval_criteria) | 0 | Evaluation criteria definitions for task quality assessment |
| [`experiments`](#experiments) | 175 | One experiment per research question — parent of runs |
| [`goal_attempt`](#goal_attempt) | 1,836 | Individual goal attempt records within goal execution sessions |
| [`goal_execution`](#goal_execution) | 1,553 | Goal execution session records — multi-step agentic task tracking |
| [`gpu_config`](#gpu_config) | 0 | GPU configuration snapshot captured at experiment time |
| [`gpu_samples`](#gpu_samples) | 45,130 | DCGM GPU utilization and memory (NVIDIA Grace only) |
| [`hallucination_events`](#hallucination_events) | 0 | Detected hallucination events with classification and energy cost |
| [`hardware_config`](#hardware_config) | 2 | hw_config.json snapshot captured at experiment time |
| [`idle_baseline_domains`](#idle_baseline_domains) | 53 | Per-domain idle baseline power values (pkg, core, uncore, dram, gpu) |
| [`idle_baselines`](#idle_baselines) | 11 | Idle energy baseline measurements per platform |
| [`interrupt_samples`](#interrupt_samples) | 337,654 | Context switches and interrupt ticks at 10Hz |
| [`io_samples`](#io_samples) | 336,075 | Disk read/write byte deltas at 10Hz |
| [`llm_interactions`](#llm_interactions) | 7,071 | One row per LLM API call within a run |
| [`machine_setup_history`](#machine_setup_history) | 0 | Machine provisioning and configuration change history |
| [`measurement_method_registry`](#measurement_method_registry) | 92 | Master registry of all measurement methods with formula and provenance |
| [`measurement_methodology`](#measurement_methodology) | 109,438 | Per-run methodology audit trail — links runs to method_registry entries |
| [`method_references`](#method_references) | 108 | Literature citations per measurement method |
| [`metric_analysis_domains`](#metric_analysis_domains) | 132 | Analysis domain assignments for metrics in the view system |
| [`metric_display_registry`](#metric_display_registry) | 0 | Display configuration for all metrics in GUI and reports |
| [`migration_history`](#migration_history) | 19 | Applied migration versions with checksums |
| [`network_energy_attribution`](#network_energy_attribution) | 1,489 | Network wait energy attribution per run and phase |
| [`nic_samples`](#nic_samples) | 307,647 | Network interface byte and packet counters at 10Hz |
| [`normalization_factors`](#normalization_factors) | 1,554 | Grid intensity and environmental conversion factors per country |
| [`orchestration_events`](#orchestration_events) | 2,756 | Timeline of agentic orchestration phase transitions |
| [`orchestration_tax_summary`](#orchestration_tax_summary) | 173 | Pre-computed orchestration tax summary per experiment |
| [`outlier_detection_config`](#outlier_detection_config) | 11 | Outlier detection thresholds and domain rules |
| [`output_quality`](#output_quality) | 0 | LLM output quality scores per run |
| [`output_quality_judges`](#output_quality_judges) | 0 | Judge model configurations for LLM-as-judge quality evaluation |
| [`page_configs`](#page_configs) | 0 | GUI page layout configurations |
| [`page_metric_configs`](#page_metric_configs) | 0 | Metric display configurations per GUI page |
| [`page_sections`](#page_sections) | 0 | GUI page section definitions |
| [`page_templates`](#page_templates) | 0 | GUI page template definitions |
| [`platform_domain_relationships`](#platform_domain_relationships) | 0 | Platform-to-energy-domain mapping for cross-platform queries |
| [`power_limit_events`](#power_limit_events) | 0 | Thermal throttle and power limit events during runs |
| [`power_limits`](#power_limits) | 4 | Platform thermal design power limits |
| [`power_rail_samples`](#power_rail_samples) | 275,220 | SPBM per-rail power readings (NVIDIA Grace only) |
| [`power_rails`](#power_rails) | 10 | SPBM power rail definitions (NVIDIA Grace only) |
| [`query_registry`](#query_registry) | 0 | All SQL queries — no SQL hardcoded in application code |
| [`retry_policy`](#retry_policy) | 3 | Retry behavior configuration per failure type |
| [`run_outliers`](#run_outliers) | 22 | Outlier detection results per run with classification and severity |
| [`run_power_limits`](#run_power_limits) | 984 | Power limit state snapshots captured during runs |
| [`run_quality`](#run_quality) | 1,554 | Composite run quality scores across multiple quality dimensions |
| [`runs`](#runs) | 1,554 | One row per linear or agentic workflow execution (153 columns) |
| [`schema_version`](#schema_version) | 23 | Current schema version tracking |
| [`sqlite_sequence`](#sqlite_sequence) | 33 | SQLite internal auto-increment sequence tracking |
| [`standardization_registry`](#standardization_registry) | 0 | Metric standardization parameters for cross-platform normalization |
| [`task_categories`](#task_categories) | 65 | Task definitions loaded from config/tasks.yaml |
| [`task_quality_config`](#task_quality_config) | 0 | Quality thresholds per task type |
| [`task_retry_override`](#task_retry_override) | 0 | Per-task retry policy overrides |
| [`thermal_samples`](#thermal_samples) | 34,904 | Temperature, fan RPM, voltage at 1Hz |
| [`thermal_samples_v2`](#thermal_samples_v2) | 29,778 | Thermal samples schema v2 with zone registry integration |
| [`thermal_zones`](#thermal_zones) | 7 | Thermal zone inventory and configuration per platform |
| [`tool_failure_events`](#tool_failure_events) | 287 | Tool execution failure events with classification and recovery data |

---

## Views

A-LEMS maintains a set of filtered views for analysis. Views prefixed `v_runs_clean_` exclude confirmed outliers. Views prefixed `v_runs_measured_` exclude data quality failures but retain statistical anomalies for distribution analysis.

| View | Purpose |
|---|---|
| `energy_samples_with_power` |  |
| `ml_features` |  |
| `orchestration_analysis` |  |
| `research_metrics_view` |  |
| `v_attribution_summary` |  |
| `v_energy` |  |
| `v_energy_normalized` |  |
| `v_failure_energy_taxonomy` |  |
| `v_fraction_verification` |  |
| `v_goal_energy_decomposition` |  |
| `v_idle_baseline_domains` |  |
| `v_idle_baselines` |  |
| `v_outcome_efficiency` |  |
| `v_platform_baseline_summary` |  |
| `v_quality_energy_frontier` |  |
| `v_runs_clean` | Excludes confirmed outliers of any class |
| `v_runs_clean_cpu` | Excludes confirmed outliers of any class |
| `v_runs_clean_energy` | Excludes confirmed outliers of any class |
| `v_runs_clean_llm` | Excludes confirmed outliers of any class |
| `v_runs_clean_orchestration` | Excludes confirmed outliers of any class |
| `v_runs_clean_system` | Excludes confirmed outliers of any class |
| `v_runs_clean_thermal` | Excludes confirmed outliers of any class |
| `v_runs_measured_cpu` | Excludes data quality failures, retains statistical anomalies |
| `v_runs_measured_energy` | Excludes data quality failures, retains statistical anomalies |
| `v_runs_measured_llm` | Excludes data quality failures, retains statistical anomalies |
| `v_runs_measured_orchestration` | Excludes data quality failures, retains statistical anomalies |
| `v_runs_measured_system` | Excludes data quality failures, retains statistical anomalies |
| `v_runs_measured_thermal` | Excludes data quality failures, retains statistical anomalies |
| `v_runs_unfiltered` | All runs including confirmed outliers |
| `v_thermal_cpu` |  |

---

## Column Reference


### `analysis_domain_config`

**Rows:** 10 — Analysis domain definitions for ETL and reporting

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `domain_name` | TEXT |  |  |
| 1 | `is_foundation` | INTEGER |  |  |
| 2 | `description` | TEXT |  |  |
| 3 | `column_count` | INTEGER |  |  |
| 4 | `created_at` | TIMESTAMP |  |  |

### `analysis_view_config`

**Rows:** 8 — View configuration for the clean/measured view system

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `view_name` | TEXT |  |  |
| 1 | `domain_name` | TEXT |  |  |
| 2 | `include_foundation` | INTEGER |  |  |

### `audit_log`

**Rows:** 0 — Run-level audit events — tracks metric updates and data quality changes

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER (nullable) | SYSTEM |  |
| 2 | `event_type` | TEXT (nullable) |  |  |
| 3 | `event_detail` | TEXT (nullable) |  |  |
| 4 | `metric_id` | TEXT (nullable) |  |  |
| 5 | `value_before` | TEXT (nullable) |  |  |
| 6 | `value_after` | TEXT (nullable) |  |  |
| 7 | `hw_context` | TEXT (nullable) |  |  |
| 8 | `logged_at` | TEXT (nullable) |  |  |

### `component_registry`

**Rows:** 0 — Registered system components with schema and data shape definitions

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `name` | TEXT |  |  |
| 1 | `group_name` | TEXT (nullable) |  |  |
| 2 | `description` | TEXT (nullable) |  |  |
| 3 | `props_schema` | TEXT (nullable) |  |  |
| 4 | `data_shape` | TEXT (nullable) |  |  |
| 5 | `has_3d_twin` | TEXT (nullable) |  |  |
| 6 | `export_pdf` | INTEGER (nullable) |  |  |
| 7 | `export_png` | INTEGER (nullable) |  |  |
| 8 | `export_csv` | INTEGER (nullable) |  |  |
| 9 | `available_in` | TEXT (nullable) |  |  |
| 10 | `active` | INTEGER (nullable) |  |  |

### `cooling_devices`

**Rows:** 27 — Cooling device inventory per platform (fans, liquid coolers)

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `device_id` | INTEGER |  |  |
| 1 | `machine_id` | TEXT |  |  |
| 2 | `device_type` | TEXT |  |  |
| 3 | `device_index` | INTEGER |  |  |
| 4 | `driver` | TEXT (nullable) |  |  |
| 5 | `device` | TEXT (nullable) |  |  |
| 6 | `canonical_role` | TEXT | MEASURED (os) | Normalized Per-Zone Thermal Reader V2 |
| 7 | `source_subsystem` | TEXT |  |  |
| 8 | `max_state` | INTEGER |  |  |
| 9 | `first_seen` | TEXT |  |  |
| 10 | `last_seen` | TEXT |  |  |
| 11 | `active` | INTEGER |  |  |

### `cooling_samples`

**Rows:** 40,284 — Cooling device state and target temperature at 1Hz

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER | SYSTEM |  |
| 2 | `device_id` | INTEGER |  |  |
| 3 | `timestamp_ns` | INTEGER |  |  |
| 4 | `cur_state` | INTEGER | MEASURED (os) | Cooling Device State Reader V1 |
| 5 | `quality_flag` | TEXT | MEASURED (os) | Normalized Per-Zone Thermal Reader V2 |
| 6 | `invalid_reason` | TEXT (nullable) |  |  |
| 7 | `global_run_id` | TEXT (nullable) | SYSTEM |  |

### `cpu_idle_states`

**Rows:** 5,968 — CPU C-state residency data per platform

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER | SYSTEM |  |
| 2 | `platform` | TEXT |  |  |
| 3 | `state_name` | TEXT |  |  |
| 4 | `depth_rank` | INTEGER |  |  |
| 5 | `residency_seconds` | REAL |  |  |
| 6 | `residency_type` | TEXT |  |  |
| 7 | `measurement_source` | TEXT |  |  |

### `cpu_samples`

**Rows:** 276 — CPU frequency, IPC, cache counters at 10Hz

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `sample_id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER | SYSTEM |  |
| 2 | `timestamp_ns` | INTEGER |  |  |
| 3 | `cpu_util_percent` | REAL (nullable) |  |  |
| 4 | `cpu_busy_mhz` | REAL (nullable) | MEASURED (silicon) | Intel Turbostat CPU Frequency Reader |
| 5 | `cpu_avg_mhz` | REAL (nullable) | MEASURED (silicon) | Intel Turbostat CPU Frequency Reader |
| 6 | `c1_residency` | REAL (nullable) |  |  |
| 7 | `c2_residency` | REAL (nullable) |  |  |
| 8 | `c3_residency` | REAL (nullable) |  |  |
| 9 | `c6_residency` | REAL (nullable) |  |  |
| 10 | `c7_residency` | REAL (nullable) |  |  |
| 11 | `pkg_c8_residency` | REAL (nullable) |  |  |
| 12 | `pkg_c9_residency` | REAL (nullable) |  |  |
| 13 | `pkg_c10_residency` | REAL (nullable) |  |  |
| 14 | `package_power` | REAL (nullable) |  |  |
| 15 | `dram_power` | REAL (nullable) |  |  |
| 16 | `gpu_rc6` | REAL (nullable) |  |  |
| 17 | `package_temp` | REAL (nullable) |  |  |
| 18 | `ipc` | REAL (nullable) | CALCULATED (silicon) | Instructions Per Cycle (IPC) |
| 19 | `l1d_cache_misses` | BIGINT (nullable) |  |  |
| 20 | `l2_cache_misses` | BIGINT (nullable) |  |  |
| 21 | `l3_cache_hits` | BIGINT (nullable) |  |  |
| 22 | `l3_cache_misses` | BIGINT (nullable) |  |  |
| 23 | `extra_metrics_json` | TEXT (nullable) |  |  |
| 24 | `sample_start_ns` | INTEGER (nullable) |  |  |
| 25 | `sample_end_ns` | INTEGER (nullable) |  |  |
| 26 | `interval_ns` | INTEGER (nullable) |  |  |
| 27 | `global_run_id` | TEXT (nullable) | SYSTEM |  |
| 28 | `measurement_source` | TEXT (nullable) |  |  |

### `device_telemetry`

**Rows:** 501 — Generic device telemetry samples for non-standard hardware

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `telemetry_id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER | SYSTEM |  |
| 2 | `global_run_id` | TEXT (nullable) | SYSTEM |  |
| 3 | `source_id` | INTEGER |  |  |
| 4 | `timestamp_ns` | BIGINT |  |  |
| 5 | `interval_ns` | BIGINT |  |  |
| 6 | `device_type` | TEXT |  |  |
| 7 | `power_mw` | REAL (nullable) |  |  |
| 8 | `energy_uj` | REAL (nullable) |  |  |
| 9 | `util_pct` | REAL (nullable) |  |  |
| 10 | `temp_c` | REAL (nullable) |  |  |
| 11 | `clock_mhz` | REAL (nullable) |  |  |
| 12 | `dc_input_mw` | REAL (nullable) |  |  |
| 13 | `mem_util_pct` | REAL (nullable) |  |  |

### `energy_attribution`

**Rows:** 1,546 — Phase-attributed energy values per run (planning, tool, synthesis)

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `run_id` | INTEGER | SYSTEM |  |
| 1 | `pkg_energy_uj` | BIGINT (nullable) | MEASURED (silicon) | RAPL MSR Direct Package Energy |
| 2 | `core_energy_uj` | BIGINT (nullable) | MEASURED (silicon) | RAPL MSR Direct Package Energy |
| 3 | `dram_energy_uj` | BIGINT (nullable) | MEASURED (silicon) | RAPL MSR Direct Package Energy |
| 4 | `uncore_energy_uj` | BIGINT (nullable) | MEASURED (silicon) | RAPL MSR Direct Package Energy |
| 5 | `background_energy_uj` | BIGINT (nullable) |  |  |
| 6 | `interrupt_energy_uj` | BIGINT (nullable) |  |  |
| 7 | `scheduler_energy_uj` | BIGINT (nullable) |  |  |
| 8 | `network_wait_energy_uj` | BIGINT (nullable) |  |  |
| 9 | `io_wait_energy_uj` | BIGINT (nullable) |  |  |
| 10 | `disk_energy_uj` | BIGINT (nullable) |  |  |
| 11 | `memory_pressure_energy_uj` | BIGINT (nullable) |  |  |
| 12 | `cache_dram_energy_uj` | BIGINT (nullable) |  |  |
| 13 | `orchestration_energy_uj` | BIGINT (nullable) |  |  |
| 14 | `planning_energy_uj` | BIGINT (nullable) | MEASURED (orchestration) | Phase Attribution v2 (Direct RAPL Sample Measurement) |
| 15 | `execution_energy_uj` | BIGINT (nullable) | MEASURED (orchestration) | Phase Attribution v2 (Direct RAPL Sample Measurement) |
| 16 | `synthesis_energy_uj` | BIGINT (nullable) | MEASURED (orchestration) | Phase Attribution v2 (Direct RAPL Sample Measurement) |
| 17 | `inter_phase_energy_uj` | BIGINT (nullable) | MEASURED (orchestration) | Phase Attribution v2 (Direct RAPL Sample Measurement) |
| 18 | `tool_energy_uj` | BIGINT (nullable) |  |  |
| 19 | `retry_energy_uj` | BIGINT (nullable) |  |  |
| 20 | `failed_tool_energy_uj` | BIGINT (nullable) |  |  |
| 21 | `rejected_generation_energy_uj` | BIGINT (nullable) |  |  |
| 22 | `llm_compute_energy_uj` | BIGINT (nullable) |  |  |
| 23 | `prefill_energy_uj` | BIGINT (nullable) |  |  |
| 24 | `decode_energy_uj` | BIGINT (nullable) |  |  |
| 25 | `energy_per_completion_token_uj` | REAL (nullable) |  |  |
| 26 | `energy_per_successful_step_uj` | REAL (nullable) |  |  |
| 27 | `energy_per_accepted_answer_uj` | REAL (nullable) |  |  |
| 28 | `energy_per_solved_task_uj` | REAL (nullable) |  |  |
| 29 | `thermal_penalty_energy_uj` | BIGINT (nullable) |  |  |
| 30 | `thermal_penalty_time_ms` | REAL (nullable) |  |  |
| 31 | `unattributed_energy_uj` | BIGINT (nullable) |  |  |
| 32 | `attribution_coverage_pct` | REAL (nullable) |  |  |
| 33 | `attribution_model_version` | TEXT (nullable) |  |  |
| 34 | `llm_wait_energy_uj` | BIGINT (nullable) |  |  |
| 35 | `attribution_method` | TEXT (nullable) |  |  |
| 36 | `ml_model_version` | TEXT (nullable) |  |  |
| 37 | `ttft_ms` | REAL (nullable) | MEASURED (application) | Time to First Token Measurement |
| 38 | `tpot_ms` | REAL (nullable) | MEASURED (application) | Time Per Output Token Measurement |
| 39 | `created_at` | TIMESTAMP (nullable) |  |  |
| 40 | `updated_at` | TIMESTAMP (nullable) |  |  |
| 41 | `gpu_llm_compute_energy_uj` | BIGINT (nullable) | CALCULATED (application) | GPU Dynamic Energy Attribution (Exclusive Workload) |
| 42 | `gpu_orchestration_energy_uj` | BIGINT (nullable) | CALCULATED (application) | GPU Dynamic Energy Attribution (Exclusive Workload) |
| 43 | `gpu_phase_planning_uj` | BIGINT (nullable) | INFERRED (application) | GPU Phase Energy Alignment (CPU Proxy) |
| 44 | `gpu_phase_execution_uj` | BIGINT (nullable) | INFERRED (application) | GPU Phase Energy Alignment (CPU Proxy) |
| 45 | `gpu_phase_synthesis_uj` | BIGINT (nullable) | INFERRED (application) | GPU Phase Energy Alignment (CPU Proxy) |
| 46 | `gpu_phase_inter_uj` | BIGINT (nullable) | INFERRED (application) | GPU Phase Energy Alignment (CPU Proxy) |

### `energy_derived_metrics`

**Rows:** 8,320 — Computed energy metrics derived from raw samples via ETL

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `metric_id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER | SYSTEM |  |
| 2 | `global_run_id` | TEXT (nullable) | SYSTEM |  |
| 3 | `sample_id` | INTEGER (nullable) |  |  |
| 4 | `metric_name` | TEXT |  |  |
| 5 | `value_uj` | REAL (nullable) |  |  |
| 6 | `derivation_formula` | TEXT |  |  |
| 7 | `source_ids_used` | TEXT |  |  |

### `energy_domains`

**Rows:** 29 — Energy domain taxonomy (pkg, core, uncore, dram, gpu)

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `domain_id` | INTEGER |  |  |
| 1 | `name` | TEXT |  |  |
| 2 | `description` | TEXT (nullable) |  |  |
| 3 | `parent_domain_id` | INTEGER (nullable) |  |  |
| 4 | `is_leaf` | BOOLEAN |  |  |
| 5 | `is_cumulative` | BOOLEAN |  |  |
| 6 | `unit` | TEXT |  |  |
| 7 | `reader_keys` | TEXT (nullable) |  |  |
| 8 | `legacy_column` | TEXT (nullable) |  |  |

### `energy_sample_domains`

**Rows:** 3,206,530 — Domain assignments for energy samples (pkg, core, uncore, dram, gpu)

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `sample_id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER | SYSTEM |  |
| 2 | `global_run_id` | TEXT (nullable) | SYSTEM |  |
| 3 | `domain_id` | INTEGER |  |  |
| 4 | `source_id` | INTEGER |  |  |
| 5 | `energy_uj` | REAL |  |  |

### `energy_samples`

**Rows:** 0 — Raw RAPL/SPBM/IOKit counter reads at 100Hz

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `sample_id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER | SYSTEM |  |
| 2 | `timestamp_ns` | INTEGER |  |  |
| 3 | `pkg_energy_uj` | INTEGER (nullable) | MEASURED (silicon) | RAPL MSR Direct Package Energy |
| 4 | `core_energy_uj` | INTEGER (nullable) | MEASURED (silicon) | RAPL MSR Direct Package Energy |
| 5 | `uncore_energy_uj` | INTEGER (nullable) | MEASURED (silicon) | RAPL MSR Direct Package Energy |
| 6 | `dram_energy_uj` | INTEGER (nullable) | MEASURED (silicon) | RAPL MSR Direct Package Energy |
| 7 | `pkg_start_uj` | INTEGER (nullable) |  |  |
| 8 | `pkg_end_uj` | INTEGER (nullable) |  |  |
| 9 | `core_start_uj` | INTEGER (nullable) |  |  |
| 10 | `core_end_uj` | INTEGER (nullable) |  |  |
| 11 | `dram_start_uj` | INTEGER (nullable) |  |  |
| 12 | `dram_end_uj` | INTEGER (nullable) |  |  |
| 13 | `uncore_start_uj` | INTEGER (nullable) |  |  |
| 14 | `uncore_end_uj` | INTEGER (nullable) |  |  |
| 15 | `sample_start_ns` | INTEGER (nullable) |  |  |
| 16 | `sample_end_ns` | INTEGER (nullable) |  |  |
| 17 | `interval_ns` | INTEGER (nullable) |  |  |
| 18 | `gpu_start_uj` | INTEGER (nullable) |  |  |
| 19 | `gpu_end_uj` | INTEGER (nullable) |  |  |
| 20 | `gpu_energy_uj` | INTEGER (nullable) |  |  |
| 21 | `global_run_id` | TEXT (nullable) | SYSTEM |  |

### `energy_samples_v2`

**Rows:** 336,217 — Energy samples schema v2 with domain registry integration

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `sample_id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER | SYSTEM |  |
| 2 | `global_run_id` | TEXT (nullable) | SYSTEM |  |
| 3 | `source_id` | INTEGER |  |  |
| 4 | `timestamp_ns` | BIGINT |  |  |
| 5 | `interval_ns` | BIGINT |  |  |

### `energy_sources`

**Rows:** 9 — Energy source registry (rapl, spbm, iokit, dcgm, arm_pmu)

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `source_id` | INTEGER |  |  |
| 1 | `name` | TEXT |  |  |
| 2 | `description` | TEXT (nullable) |  |  |
| 3 | `confidence` | REAL |  |  |
| 4 | `provenance` | TEXT |  |  |
| 5 | `layer` | TEXT |  |  |

### `environment_config`

**Rows:** 67 — Software environment fingerprint — git commit, package versions

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `env_id` | INTEGER |  |  |
| 1 | `env_hash` | TEXT (nullable) |  |  |
| 2 | `python_version` | TEXT (nullable) |  |  |
| 3 | `python_implementation` | TEXT (nullable) |  |  |
| 4 | `os_name` | TEXT (nullable) |  |  |
| 5 | `os_version` | TEXT (nullable) |  |  |
| 6 | `kernel_version` | TEXT (nullable) |  |  |
| 7 | `llm_framework` | TEXT (nullable) |  |  |
| 8 | `framework_version` | TEXT (nullable) |  |  |
| 9 | `git_commit` | TEXT (nullable) |  |  |
| 10 | `git_branch` | TEXT (nullable) |  |  |
| 11 | `git_dirty` | BOOLEAN (nullable) |  |  |
| 12 | `numpy_version` | TEXT (nullable) |  |  |
| 13 | `torch_version` | TEXT (nullable) |  |  |
| 14 | `transformers_version` | TEXT (nullable) |  |  |
| 15 | `container_runtime` | TEXT (nullable) |  |  |
| 16 | `container_image` | TEXT (nullable) |  |  |
| 17 | `schema_version` | INTEGER (nullable) |  |  |
| 18 | `created_at` | TIMESTAMP (nullable) |  |  |

### `etl_queue`

**Rows:** 672 — Pending and completed ETL job tracking

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `queue_id` | INTEGER |  |  |
| 1 | `entity_type` | TEXT |  |  |
| 2 | `entity_id` | INTEGER |  |  |
| 3 | `etl_name` | TEXT |  |  |
| 4 | `status` | TEXT |  |  |
| 5 | `error_message` | TEXT (nullable) |  |  |
| 6 | `created_at` | TIMESTAMP (nullable) |  |  |
| 7 | `processed_at` | TIMESTAMP (nullable) |  |  |

### `eval_criteria`

**Rows:** 0 — Evaluation criteria definitions for task quality assessment

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `id` | INTEGER |  |  |
| 1 | `goal_id` | TEXT |  |  |
| 2 | `stat_test` | TEXT (nullable) |  |  |
| 3 | `alpha` | REAL (nullable) |  |  |
| 4 | `effect_size` | TEXT (nullable) |  |  |
| 5 | `min_runs_per_group` | INTEGER (nullable) |  |  |
| 6 | `report_ci` | INTEGER (nullable) |  |  |
| 7 | `ci_level` | REAL (nullable) |  |  |
| 8 | `comparison_mode` | TEXT (nullable) |  |  |
| 9 | `created_at` | TEXT (nullable) |  |  |

### `experiments`

**Rows:** 175 — One experiment per research question — parent of runs

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `exp_id` | INTEGER | SYSTEM |  |
| 1 | `name` | TEXT |  |  |
| 2 | `description` | TEXT (nullable) |  |  |
| 3 | `workflow_type` | TEXT (nullable) | SYSTEM |  |
| 4 | `model_name` | TEXT (nullable) |  |  |
| 5 | `model_id` | TEXT (nullable) |  |  |
| 6 | `execution_site` | TEXT (nullable) |  |  |
| 7 | `transport` | TEXT (nullable) |  |  |
| 8 | `remote_energy_available` | INTEGER (nullable) |  |  |
| 9 | `provider` | TEXT (nullable) |  |  |
| 10 | `task_name` | TEXT (nullable) |  |  |
| 11 | `country_code` | TEXT (nullable) |  |  |
| 12 | `created_at` | TIMESTAMP (nullable) |  |  |
| 13 | `group_id` | TEXT (nullable) |  |  |
| 14 | `status` | TEXT (nullable) |  |  |
| 15 | `started_at` | TIMESTAMP (nullable) |  |  |
| 16 | `completed_at` | TIMESTAMP (nullable) |  |  |
| 17 | `error_message` | TEXT (nullable) |  |  |
| 18 | `runs_completed` | INTEGER (nullable) |  |  |
| 19 | `runs_total` | INTEGER (nullable) |  |  |
| 20 | `optimization_enabled` | INTEGER (nullable) |  |  |
| 21 | `experiment_type` | TEXT | MEASURED (orchestration) | Experiment Classification Metadata |
| 22 | `experiment_goal` | TEXT (nullable) | MEASURED (orchestration) | Experiment Classification Metadata |
| 23 | `experiment_notes` | TEXT (nullable) | MEASURED (orchestration) | Experiment Classification Metadata |
| 24 | `hw_id` | INTEGER (nullable) | SYSTEM |  |
| 25 | `env_id` | INTEGER (nullable) |  |  |
| 26 | `is_valid` | INTEGER |  |  |
| 27 | `invalidation_reason` | TEXT (nullable) |  |  |
| 28 | `invalidated_at` | TIMESTAMP (nullable) |  |  |
| 29 | `global_exp_id` | TEXT (nullable) |  |  |

### `goal_attempt`

**Rows:** 1,836 — Individual goal attempt records within goal execution sessions

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `attempt_id` | INTEGER |  |  |
| 1 | `goal_id` | INTEGER |  |  |
| 2 | `run_id` | INTEGER | SYSTEM |  |
| 3 | `attempt_number` | INTEGER |  |  |
| 4 | `is_winning` | INTEGER |  |  |
| 5 | `outcome` | TEXT |  |  |
| 6 | `failure_cause` | TEXT (nullable) |  |  |
| 7 | `energy_uj` | INTEGER (nullable) |  |  |
| 8 | `orchestration_uj` | INTEGER (nullable) |  |  |
| 9 | `compute_uj` | INTEGER (nullable) |  |  |
| 10 | `normalized_score` | REAL (nullable) |  |  |
| 11 | `pass_fail` | INTEGER (nullable) |  |  |
| 12 | `status` | TEXT |  |  |
| 13 | `started_at` | TIMESTAMP (nullable) |  |  |
| 14 | `finished_at` | TIMESTAMP (nullable) |  |  |
| 15 | `updated_at` | TIMESTAMP (nullable) |  |  |
| 16 | `created_at` | TIMESTAMP (nullable) |  |  |
| 17 | `gpu_energy_uj` | INTEGER (nullable) |  |  |
| 18 | `is_retry` | INTEGER |  |  |
| 19 | `retry_of_attempt_id` | INTEGER (nullable) |  |  |
| 20 | `failure_type` | TEXT (nullable) |  |  |

### `goal_execution`

**Rows:** 1,553 — Goal execution session records — multi-step agentic task tracking

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `goal_id` | INTEGER |  |  |
| 1 | `exp_id` | INTEGER | SYSTEM |  |
| 2 | `first_run_id` | INTEGER (nullable) |  |  |
| 3 | `goal_description` | TEXT |  |  |
| 4 | `goal_type` | TEXT |  |  |
| 5 | `workflow_type` | TEXT | SYSTEM |  |
| 6 | `difficulty_level` | TEXT (nullable) |  |  |
| 7 | `total_attempts` | INTEGER |  |  |
| 8 | `success` | INTEGER |  |  |
| 9 | `winning_run_id` | INTEGER (nullable) |  |  |
| 10 | `total_energy_uj` | INTEGER (nullable) | CALCULATED (silicon) | Dynamic Energy Calculation |
| 11 | `successful_energy_uj` | INTEGER (nullable) |  |  |
| 12 | `overhead_energy_uj` | INTEGER (nullable) |  |  |
| 13 | `overhead_fraction` | REAL (nullable) |  |  |
| 14 | `orchestration_fraction` | REAL (nullable) |  |  |
| 15 | `wall_time_ms` | REAL (nullable) |  |  |
| 16 | `task_id` | TEXT (nullable) |  |  |
| 17 | `status` | TEXT |  |  |
| 18 | `started_at` | TIMESTAMP (nullable) |  |  |
| 19 | `finished_at` | TIMESTAMP (nullable) |  |  |
| 20 | `updated_at` | TIMESTAMP (nullable) |  |  |
| 21 | `created_at` | TIMESTAMP (nullable) |  |  |
| 22 | `gpu_total_energy_uj` | INTEGER (nullable) | MEASURED (silicon) | Intel Iris Xe GPU Energy via MSR 0x641 |
| 23 | `gpu_pct_of_pkg` | REAL (nullable) | CALCULATED (silicon) | GPU Dynamic Energy via Run-Local Adaptive Idle Baseline |

### `gpu_config`

**Rows:** 0 — GPU configuration snapshot captured at experiment time

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `gpu_id` | INTEGER |  |  |
| 1 | `gpu_index` | INTEGER |  |  |
| 2 | `vendor` | TEXT |  |  |
| 3 | `model` | TEXT |  |  |
| 4 | `driver_version` | TEXT (nullable) |  |  |
| 5 | `cuda_version` | TEXT (nullable) |  |  |
| 6 | `rocm_version` | TEXT (nullable) |  |  |
| 7 | `vbios_version` | TEXT (nullable) |  |  |
| 8 | `pci_id` | TEXT (nullable) |  |  |
| 9 | `memory_total_mb` | INTEGER (nullable) |  |  |
| 10 | `energy_supported` | INTEGER |  |  |
| 11 | `backend` | TEXT (nullable) |  |  |
| 12 | `gpu_hash` | TEXT |  |  |
| 13 | `created_at` | TIMESTAMP (nullable) |  |  |

### `gpu_samples`

**Rows:** 45,130 — DCGM GPU utilization and memory (NVIDIA Grace only)

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `sample_id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER | SYSTEM |  |
| 2 | `gpu_index` | INTEGER |  |  |
| 3 | `sample_start_ns` | BIGINT |  |  |
| 4 | `sample_end_ns` | BIGINT |  |  |
| 5 | `interval_ns` | BIGINT |  |  |
| 6 | `energy_start_uj` | BIGINT (nullable) |  |  |
| 7 | `energy_end_uj` | BIGINT (nullable) |  |  |
| 8 | `energy_uj` | BIGINT (nullable) |  |  |
| 9 | `power_mw` | INTEGER (nullable) |  |  |
| 10 | `util_gpu_pct` | REAL (nullable) |  |  |
| 11 | `util_mem_pct` | REAL (nullable) |  |  |
| 12 | `sm_clock_mhz` | INTEGER (nullable) |  |  |
| 13 | `mem_clock_mhz` | INTEGER (nullable) |  |  |
| 14 | `mem_used_mb` | INTEGER (nullable) |  |  |
| 15 | `temperature_c` | INTEGER (nullable) |  |  |
| 16 | `source` | TEXT |  |  |

### `hallucination_events`

**Rows:** 0 — Detected hallucination events with classification and energy cost

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `hallucination_id` | INTEGER |  |  |
| 1 | `attempt_id` | INTEGER |  |  |
| 2 | `goal_id` | INTEGER |  |  |
| 3 | `decision_id` | INTEGER (nullable) |  |  |
| 4 | `interaction_id` | INTEGER (nullable) |  |  |
| 5 | `orchestration_event_id` | INTEGER (nullable) |  |  |
| 6 | `hallucination_type` | TEXT |  |  |
| 7 | `detection_method` | TEXT |  |  |
| 8 | `detection_confidence` | REAL (nullable) |  |  |
| 9 | `semantic_similarity` | REAL (nullable) |  |  |
| 10 | `severity` | REAL (nullable) |  |  |
| 11 | `expected_output` | TEXT (nullable) |  |  |
| 12 | `actual_output` | TEXT (nullable) |  |  |
| 13 | `wasted_energy_uj` | INTEGER (nullable) |  |  |
| 14 | `detected_at` | TIMESTAMP |  |  |
| 15 | `wasted_energy_uj_real` | INTEGER (nullable) |  |  |

### `hardware_config`

**Rows:** 2 — hw_config.json snapshot captured at experiment time

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `hw_id` | INTEGER | SYSTEM |  |
| 1 | `hardware_hash` | TEXT (nullable) |  |  |
| 2 | `hostname` | TEXT (nullable) |  |  |
| 3 | `cpu_model` | TEXT (nullable) |  |  |
| 4 | `cpu_cores` | INTEGER (nullable) |  |  |
| 5 | `cpu_threads` | INTEGER (nullable) |  |  |
| 6 | `cpu_architecture` | TEXT (nullable) |  |  |
| 7 | `cpu_vendor` | TEXT (nullable) |  |  |
| 8 | `cpu_family` | INTEGER (nullable) |  |  |
| 9 | `cpu_model_id` | INTEGER (nullable) |  |  |
| 10 | `cpu_stepping` | INTEGER (nullable) |  |  |
| 11 | `has_avx2` | BOOLEAN (nullable) |  |  |
| 12 | `has_avx512` | BOOLEAN (nullable) |  |  |
| 13 | `has_vmx` | BOOLEAN (nullable) |  |  |
| 14 | `gpu_model` | TEXT (nullable) |  |  |
| 15 | `gpu_driver` | TEXT (nullable) |  |  |
| 16 | `gpu_count` | INTEGER (nullable) | MEASURED (silicon) | GPU Idle Baseline (2-Sigma Method) |
| 17 | `gpu_power_available` | BOOLEAN (nullable) |  |  |
| 18 | `ram_gb` | REAL (nullable) |  |  |
| 19 | `kernel_version` | TEXT (nullable) |  |  |
| 20 | `microcode_version` | TEXT (nullable) |  |  |
| 21 | `rapl_domains` | TEXT (nullable) |  |  |
| 22 | `rapl_has_dram` | BOOLEAN (nullable) |  |  |
| 23 | `rapl_has_uncore` | BOOLEAN (nullable) |  |  |
| 24 | `system_manufacturer` | TEXT (nullable) |  |  |
| 25 | `system_product` | TEXT (nullable) |  |  |
| 26 | `system_type` | TEXT (nullable) |  |  |
| 27 | `virtualization_type` | TEXT (nullable) |  |  |
| 28 | `detected_at` | TIMESTAMP (nullable) |  |  |
| 29 | `created_at` | TIMESTAMP (nullable) |  |  |
| 30 | `last_seen` | TIMESTAMP (nullable) |  |  |
| 31 | `agent_status` | TEXT (nullable) |  |  |
| 32 | `agent_version` | TEXT (nullable) |  |  |
| 33 | `server_hw_id` | INTEGER (nullable) |  |  |

### `idle_baseline_domains`

**Rows:** 53 — Per-domain idle baseline power values (pkg, core, uncore, dram, gpu)

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `id` | INTEGER |  |  |
| 1 | `baseline_id` | TEXT | SYSTEM |  |
| 2 | `domain_id` | INTEGER |  |  |
| 3 | `power_watts` | REAL |  |  |
| 4 | `std_watts` | REAL |  |  |

### `idle_baselines`

**Rows:** 11 — Idle energy baseline measurements per platform

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `baseline_id` | TEXT | SYSTEM |  |
| 1 | `timestamp` | REAL |  |  |
| 2 | `package_power_watts` | REAL (nullable) |  |  |
| 3 | `core_power_watts` | REAL (nullable) |  |  |
| 4 | `uncore_power_watts` | REAL (nullable) |  |  |
| 5 | `dram_power_watts` | REAL (nullable) |  |  |
| 6 | `duration_seconds` | INTEGER (nullable) |  |  |
| 7 | `sample_count` | INTEGER (nullable) |  |  |
| 8 | `package_std` | REAL (nullable) |  |  |
| 9 | `core_std` | REAL (nullable) |  |  |
| 10 | `uncore_std` | REAL (nullable) |  |  |
| 11 | `dram_std` | REAL (nullable) |  |  |
| 12 | `governor` | TEXT (nullable) | SYSTEM |  |
| 13 | `turbo` | TEXT (nullable) |  |  |
| 14 | `background_cpu` | REAL (nullable) |  |  |
| 15 | `process_count` | INTEGER (nullable) | MEASURED (os) | OS Scheduler Statistics Reader |
| 16 | `method` | TEXT (nullable) |  |  |
| 17 | `gpu_power_watts` | REAL (nullable) |  |  |
| 18 | `gpu_std` | REAL (nullable) |  |  |
| 19 | `gpu_method` | TEXT (nullable) |  |  |
| 20 | `std_dev_json` | TEXT (nullable) |  |  |

### `interrupt_samples`

**Rows:** 337,654 — Context switches and interrupt ticks at 10Hz

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `sample_id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER | SYSTEM |  |
| 2 | `timestamp_ns` | INTEGER |  |  |
| 3 | `interrupts_per_sec` | REAL (nullable) |  |  |
| 4 | `interrupts_raw` | INTEGER (nullable) |  |  |
| 5 | `user_ticks_start` | INTEGER (nullable) |  |  |
| 6 | `user_ticks_end` | INTEGER (nullable) |  |  |
| 7 | `system_ticks_start` | INTEGER (nullable) |  |  |
| 8 | `system_ticks_end` | INTEGER (nullable) |  |  |
| 9 | `total_ticks_start` | INTEGER (nullable) |  |  |
| 10 | `total_ticks_end` | INTEGER (nullable) |  |  |
| 11 | `proc_ticks_start` | INTEGER (nullable) |  |  |
| 12 | `proc_ticks_end` | INTEGER (nullable) |  |  |
| 13 | `sample_start_ns` | INTEGER (nullable) |  |  |
| 14 | `sample_end_ns` | INTEGER (nullable) |  |  |
| 15 | `interval_ns` | INTEGER (nullable) |  |  |
| 16 | `global_run_id` | TEXT (nullable) | SYSTEM |  |

### `io_samples`

**Rows:** 336,075 — Disk read/write byte deltas at 10Hz

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `sample_id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER | SYSTEM |  |
| 2 | `sample_start_ns` | INTEGER |  |  |
| 3 | `sample_end_ns` | INTEGER |  |  |
| 4 | `interval_ns` | INTEGER |  |  |
| 5 | `device` | TEXT (nullable) |  |  |
| 6 | `disk_read_bytes` | BIGINT (nullable) |  |  |
| 7 | `disk_write_bytes` | BIGINT (nullable) |  |  |
| 8 | `io_block_time_ms` | REAL (nullable) |  |  |
| 9 | `disk_latency_ms` | REAL (nullable) |  |  |
| 10 | `minor_page_faults` | INTEGER (nullable) | MEASURED (silicon) | Linux perf Hardware Counters |
| 11 | `major_page_faults` | INTEGER (nullable) | MEASURED (silicon) | Linux perf Hardware Counters |

### `llm_interactions`

**Rows:** 7,071 — One row per LLM API call within a run

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `interaction_id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER | SYSTEM |  |
| 2 | `step_index` | INTEGER (nullable) |  |  |
| 3 | `workflow_type` | TEXT (nullable) | SYSTEM |  |
| 4 | `prompt` | TEXT (nullable) |  |  |
| 5 | `response` | TEXT (nullable) |  |  |
| 6 | `model_name` | TEXT (nullable) |  |  |
| 7 | `provider` | TEXT (nullable) |  |  |
| 8 | `prompt_tokens` | INTEGER (nullable) | MEASURED (application) | TTFT / TPOT Wall Clock Measurement |
| 9 | `completion_tokens` | INTEGER (nullable) | MEASURED (application) | TTFT / TPOT Wall Clock Measurement |
| 10 | `total_tokens` | INTEGER (nullable) | MEASURED (application) | TTFT / TPOT Wall Clock Measurement |
| 11 | `api_latency_ms` | REAL (nullable) | MEASURED (application) | TTFT / TPOT Wall Clock Measurement |
| 12 | `compute_time_ms` | REAL (nullable) |  |  |
| 13 | `app_throughput_kbps` | REAL (nullable) |  |  |
| 14 | `total_time_ms` | REAL (nullable) |  |  |
| 15 | `preprocess_ms` | REAL (nullable) |  |  |
| 16 | `non_local_ms` | REAL (nullable) |  |  |
| 17 | `local_compute_ms` | REAL (nullable) |  |  |
| 18 | `postprocess_ms` | REAL (nullable) |  |  |
| 19 | `cpu_percent_during_wait` | REAL (nullable) |  |  |
| 20 | `ttft_ms` | REAL (nullable) | MEASURED (application) | Time to First Token Measurement |
| 21 | `tpot_ms` | REAL (nullable) | MEASURED (application) | Time Per Output Token Measurement |
| 22 | `token_throughput` | REAL (nullable) |  |  |
| 23 | `streaming_enabled` | INTEGER (nullable) |  |  |
| 24 | `first_token_time_ns` | INTEGER (nullable) |  |  |
| 25 | `last_token_time_ns` | INTEGER (nullable) |  |  |
| 26 | `request_start_ns` | INTEGER (nullable) |  |  |
| 27 | `prefill_energy_uj` | INTEGER (nullable) |  |  |
| 28 | `bytes_sent_approx` | INTEGER (nullable) |  |  |
| 29 | `bytes_recv_approx` | INTEGER (nullable) |  |  |
| 30 | `tcp_retransmits` | INTEGER (nullable) | MEASURED (os) | Network I/O Measurement |
| 31 | `error_message` | TEXT (nullable) |  |  |
| 32 | `status` | TEXT (nullable) |  |  |
| 33 | `created_at` | TIMESTAMP (nullable) |  |  |
| 34 | `global_run_id` | TEXT (nullable) | SYSTEM |  |

### `machine_setup_history`

**Rows:** 0 — Machine provisioning and configuration change history

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `id` | INTEGER |  |  |
| 1 | `filename` | TEXT |  |  |
| 2 | `checksum_sha256` | TEXT |  |  |
| 3 | `applied_at` | TEXT |  |  |
| 4 | `tool_version` | TEXT |  |  |
| 5 | `duration_ms` | INTEGER |  |  |
| 6 | `status` | TEXT |  |  |
| 7 | `hostname` | TEXT |  |  |
| 8 | `machine_id` | TEXT (nullable) |  |  |
| 9 | `repo_commit` | TEXT (nullable) |  |  |

### `measurement_method_registry`

**Rows:** 92 — Master registry of all measurement methods with formula and provenance

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `id` | TEXT |  |  |
| 1 | `name` | TEXT |  |  |
| 2 | `version` | TEXT (nullable) |  |  |
| 3 | `description` | TEXT |  |  |
| 4 | `formula_latex` | TEXT (nullable) |  |  |
| 5 | `code_snapshot` | TEXT (nullable) |  |  |
| 6 | `code_language` | TEXT (nullable) |  |  |
| 7 | `code_version` | TEXT (nullable) |  |  |
| 8 | `parameters` | TEXT (nullable) |  |  |
| 9 | `output_metric` | TEXT (nullable) |  |  |
| 10 | `output_unit` | TEXT (nullable) |  |  |
| 11 | `provenance` | TEXT (nullable) |  |  |
| 12 | `layer` | TEXT (nullable) |  |  |
| 13 | `applicable_on` | TEXT (nullable) |  |  |
| 14 | `fallback_method_id` | TEXT (nullable) |  |  |
| 15 | `validated` | INTEGER (nullable) |  |  |
| 16 | `confidence` | REAL (nullable) |  |  |
| 17 | `validated_by` | TEXT (nullable) |  |  |
| 18 | `validated_date` | TEXT (nullable) |  |  |
| 19 | `active` | INTEGER (nullable) |  |  |
| 20 | `deprecated_reason` | TEXT (nullable) |  |  |
| 21 | `created_at` | REAL (nullable) |  |  |
| 22 | `updated_at` | REAL (nullable) |  |  |
| 23 | `doc` | TEXT (nullable) |  |  |
| 24 | `section` | TEXT (nullable) |  |  |
| 25 | `method_anchor` | TEXT (nullable) |  |  |

### `measurement_methodology`

**Rows:** 109,438 — Per-run methodology audit trail — links runs to method_registry entries

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER | SYSTEM |  |
| 2 | `metric_id` | TEXT |  |  |
| 3 | `method_id` | TEXT (nullable) |  |  |
| 4 | `parameters_used` | TEXT (nullable) |  |  |
| 5 | `value_raw` | REAL (nullable) |  |  |
| 6 | `value_unit` | TEXT (nullable) |  |  |
| 7 | `provenance` | TEXT |  |  |
| 8 | `hw_available` | INTEGER (nullable) |  |  |
| 9 | `confidence` | REAL (nullable) |  |  |
| 10 | `primary_method_failed` | INTEGER (nullable) |  |  |
| 11 | `failure_reason` | TEXT (nullable) |  |  |
| 12 | `standard_ids` | TEXT (nullable) |  |  |
| 13 | `captured_at` | REAL (nullable) |  |  |
| 14 | `method_anchor` | TEXT (nullable) |  |  |

### `method_references`

**Rows:** 108 — Literature citations per measurement method

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `id` | INTEGER |  |  |
| 1 | `method_id` | TEXT |  |  |
| 2 | `ref_type` | TEXT |  |  |
| 3 | `title` | TEXT |  |  |
| 4 | `authors` | TEXT (nullable) |  |  |
| 5 | `year` | INTEGER (nullable) |  |  |
| 6 | `venue` | TEXT (nullable) |  |  |
| 7 | `doi` | TEXT (nullable) |  |  |
| 8 | `url` | TEXT (nullable) |  |  |
| 9 | `relevance` | TEXT (nullable) |  |  |
| 10 | `cited_text` | TEXT (nullable) |  |  |
| 11 | `page_or_section` | TEXT (nullable) |  |  |
| 12 | `created_at` | TEXT (nullable) |  |  |

### `metric_analysis_domains`

**Rows:** 132 — Analysis domain assignments for metrics in the view system

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `metric_name` | TEXT |  |  |
| 1 | `domain_name` | TEXT |  |  |
| 2 | `rationale` | TEXT (nullable) |  |  |

### `metric_display_registry`

**Rows:** 0 — Display configuration for all metrics in GUI and reports

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `id` | TEXT |  |  |
| 1 | `label` | TEXT |  |  |
| 2 | `description` | TEXT (nullable) |  |  |
| 3 | `category` | TEXT (nullable) |  |  |
| 4 | `layer` | TEXT (nullable) |  |  |
| 5 | `layer_order` | INTEGER (nullable) |  |  |
| 6 | `method_id` | TEXT (nullable) |  |  |
| 7 | `unit_default` | TEXT (nullable) |  |  |
| 8 | `unit_options` | TEXT (nullable) |  |  |
| 9 | `unit_scales` | TEXT (nullable) |  |  |
| 10 | `chart_type` | TEXT (nullable) |  |  |
| 11 | `color_token` | TEXT (nullable) |  |  |
| 12 | `significance` | TEXT (nullable) |  |  |
| 13 | `direction` | TEXT (nullable) |  |  |
| 14 | `display_precision` | INTEGER (nullable) |  |  |
| 15 | `warn_threshold` | REAL (nullable) |  |  |
| 16 | `severe_threshold` | REAL (nullable) |  |  |
| 17 | `threshold_unit` | TEXT (nullable) |  |  |
| 18 | `visible_in` | TEXT (nullable) |  |  |
| 19 | `default_visible` | INTEGER (nullable) |  |  |
| 20 | `leaderboard` | INTEGER (nullable) |  |  |
| 21 | `provenance_expected` | TEXT (nullable) |  |  |
| 22 | `source_yaml` | TEXT (nullable) |  |  |
| 23 | `goal_id` | TEXT (nullable) |  |  |
| 24 | `active` | INTEGER (nullable) |  |  |
| 25 | `sort_order` | INTEGER (nullable) |  |  |
| 26 | `created_at` | REAL (nullable) |  |  |
| 27 | `updated_at` | REAL (nullable) |  |  |
| 28 | `formula_latex` | TEXT (nullable) |  |  |
| 29 | `source_description` | TEXT (nullable) |  |  |

### `migration_history`

**Rows:** 19 — Applied migration versions with checksums

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `id` | INTEGER |  |  |
| 1 | `version` | INTEGER |  |  |
| 2 | `type` | TEXT |  |  |
| 3 | `filename` | TEXT |  |  |
| 4 | `checksum_sha256` | TEXT |  |  |
| 5 | `applied_at` | TEXT |  |  |
| 6 | `tool_version` | TEXT |  |  |
| 7 | `duration_ms` | INTEGER |  |  |
| 8 | `status` | TEXT |  |  |
| 9 | `hostname` | TEXT |  |  |
| 10 | `machine_id` | TEXT (nullable) |  |  |
| 11 | `repo_commit` | TEXT (nullable) |  |  |
| 12 | `original_checksum` | TEXT (nullable) |  |  |
| 13 | `healed_at` | TEXT (nullable) |  |  |

### `network_energy_attribution`

**Rows:** 1,489 — Network wait energy attribution per run and phase

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER | SYSTEM |  |
| 2 | `strategy_used` | TEXT |  |  |
| 3 | `energy_uj` | INTEGER (nullable) |  |  |
| 4 | `confidence` | REAL |  |  |
| 5 | `measurement_type` | TEXT |  |  |
| 6 | `non_local_ms` | REAL (nullable) |  |  |
| 7 | `window_count` | INTEGER |  |  |
| 8 | `coverage_fraction` | REAL (nullable) |  |  |
| 9 | `created_at` | TEXT |  |  |
| 10 | `nic_activity_validated` | INTEGER (nullable) |  |  |
| 11 | `nic_adjusted_confidence` | REAL (nullable) |  |  |
| 12 | `nic_coverage_fraction` | REAL (nullable) |  |  |

### `nic_samples`

**Rows:** 307,647 — Network interface byte and packet counters at 10Hz

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `sample_id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER | SYSTEM |  |
| 2 | `sample_ns` | INTEGER |  |  |
| 3 | `interface` | TEXT (nullable) |  |  |
| 4 | `tx_bytes` | INTEGER (nullable) |  |  |
| 5 | `rx_bytes` | INTEGER (nullable) |  |  |
| 6 | `tx_packets` | INTEGER (nullable) |  |  |
| 7 | `rx_packets` | INTEGER (nullable) |  |  |
| 8 | `sample_start_ns` | INTEGER (nullable) |  |  |
| 9 | `sample_end_ns` | INTEGER (nullable) |  |  |

### `normalization_factors`

**Rows:** 1,554 — Grid intensity and environmental conversion factors per country

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `run_id` | INTEGER | SYSTEM |  |
| 1 | `difficulty_score` | REAL (nullable) |  |  |
| 2 | `difficulty_bucket` | TEXT (nullable) |  |  |
| 3 | `task_category` | TEXT (nullable) |  |  |
| 4 | `workload_type` | TEXT (nullable) |  |  |
| 5 | `max_step_depth` | INTEGER (nullable) |  |  |
| 6 | `branching_factor` | REAL (nullable) |  |  |
| 7 | `input_tokens` | INTEGER (nullable) |  |  |
| 8 | `output_tokens` | INTEGER (nullable) |  |  |
| 9 | `context_window_size` | INTEGER (nullable) |  |  |
| 10 | `total_work_units` | REAL (nullable) |  |  |
| 11 | `successful_goals` | INTEGER (nullable) |  |  |
| 12 | `attempted_goals` | INTEGER (nullable) |  |  |
| 13 | `failed_attempts` | INTEGER (nullable) |  |  |
| 14 | `retry_depth` | INTEGER (nullable) |  |  |
| 15 | `total_retries` | INTEGER (nullable) |  |  |
| 16 | `total_failures` | INTEGER (nullable) |  |  |
| 17 | `total_tool_calls` | INTEGER (nullable) |  |  |
| 18 | `failed_tool_calls` | INTEGER (nullable) |  |  |
| 19 | `hallucination_count` | INTEGER (nullable) |  |  |
| 20 | `hallucination_rate` | REAL (nullable) |  |  |
| 21 | `rss_memory_gb` | REAL (nullable) |  |  |
| 22 | `cache_miss_rate` | REAL (nullable) | CALCULATED (silicon) | LLC Cache Miss Rate |
| 23 | `io_wait_ratio` | REAL (nullable) |  |  |
| 24 | `stall_time_ms` | REAL (nullable) |  |  |
| 25 | `sla_violations` | INTEGER (nullable) |  |  |
| 26 | `created_at` | TIMESTAMP (nullable) |  |  |

### `orchestration_events`

**Rows:** 2,756 — Timeline of agentic orchestration phase transitions

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `event_id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER | SYSTEM |  |
| 2 | `step_index` | INTEGER (nullable) |  |  |
| 3 | `phase` | TEXT (nullable) |  |  |
| 4 | `event_type` | TEXT |  |  |
| 5 | `start_time_ns` | INTEGER | MEASURED (application) | System Wall Clock |
| 6 | `end_time_ns` | INTEGER | MEASURED (application) | System Wall Clock |
| 7 | `duration_ns` | INTEGER | MEASURED (application) | System Wall Clock |
| 8 | `power_watts` | REAL (nullable) |  |  |
| 9 | `cpu_util_percent` | REAL (nullable) |  |  |
| 10 | `interrupt_rate` | REAL (nullable) | MEASURED (os) | OS Scheduler Statistics Reader |
| 11 | `event_energy_uj` | INTEGER (nullable) |  |  |
| 12 | `tax_contribution_uj` | INTEGER (nullable) |  |  |
| 13 | `tax_percent` | REAL (nullable) |  |  |
| 14 | `global_run_id` | TEXT (nullable) | SYSTEM |  |
| 15 | `raw_energy_uj` | INTEGER (nullable) |  |  |
| 16 | `cpu_fraction_per_phase` | REAL (nullable) |  |  |
| 17 | `attributed_energy_uj` | INTEGER (nullable) | CALCULATED (os) | CPU Fraction-Based Energy Attribution |
| 18 | `attribution_method` | TEXT (nullable) |  |  |
| 19 | `quality_score` | REAL (nullable) |  |  |
| 20 | `proc_ticks_min` | INTEGER (nullable) |  |  |
| 21 | `proc_ticks_max` | INTEGER (nullable) |  |  |
| 22 | `total_ticks_min` | INTEGER (nullable) |  |  |
| 23 | `total_ticks_max` | INTEGER (nullable) |  |  |
| 24 | `created_at` | TIMESTAMP (nullable) |  |  |
| 25 | `tool_name` | TEXT (nullable) |  |  |
| 26 | `io_bytes_read` | INTEGER (nullable) |  |  |
| 27 | `io_bytes_written` | INTEGER (nullable) |  |  |
| 28 | `input_payload_hash` | TEXT (nullable) |  |  |
| 29 | `output_payload_hash` | TEXT (nullable) |  |  |
| 30 | `tool_success` | INTEGER (nullable) |  |  |
| 31 | `tool_result_rows` | INTEGER (nullable) |  |  |
| 32 | `tool_cpu_time_ns` | INTEGER (nullable) |  |  |
| 33 | `tool_memory_delta_kb` | INTEGER (nullable) |  |  |

### `orchestration_tax_summary`

**Rows:** 173 — Pre-computed orchestration tax summary per experiment

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `comparison_id` | INTEGER |  |  |
| 1 | `linear_run_id` | INTEGER |  |  |
| 2 | `agentic_run_id` | INTEGER |  |  |
| 3 | `linear_dynamic_uj` | INTEGER (nullable) |  |  |
| 4 | `agentic_dynamic_uj` | INTEGER (nullable) |  |  |
| 5 | `orchestration_tax_uj` | INTEGER (nullable) |  |  |
| 6 | `tax_percent` | REAL (nullable) |  |  |
| 7 | `linear_orchestration_uj` | INTEGER (nullable) |  |  |
| 8 | `agentic_orchestration_uj` | INTEGER (nullable) |  |  |
| 9 | `global_run_id` | TEXT (nullable) | SYSTEM |  |

### `outlier_detection_config`

**Rows:** 11 — Outlier detection thresholds and domain rules

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `config_id` | INTEGER |  |  |
| 1 | `config_version` | INTEGER |  |  |
| 2 | `method` | TEXT |  |  |
| 3 | `metric_name` | TEXT |  |  |
| 4 | `parameter_name` | TEXT |  |  |
| 5 | `parameter_value` | REAL |  |  |
| 6 | `description` | TEXT (nullable) |  |  |
| 7 | `effective_from` | TIMESTAMP |  |  |
| 8 | `effective_to` | TIMESTAMP (nullable) |  |  |
| 9 | `created_at` | TIMESTAMP |  |  |
| 10 | `outlier_class` | TEXT (nullable) |  |  |

### `output_quality`

**Rows:** 0 — LLM output quality scores per run

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `quality_id` | INTEGER |  |  |
| 1 | `attempt_id` | INTEGER |  |  |
| 2 | `goal_id` | INTEGER |  |  |
| 3 | `task_id` | TEXT (nullable) |  |  |
| 4 | `metric_type` | TEXT |  |  |
| 5 | `raw_score` | REAL (nullable) |  |  |
| 6 | `normalized_score` | REAL (nullable) |  |  |
| 7 | `pass_fail` | INTEGER (nullable) |  |  |
| 8 | `judge_method` | TEXT |  |  |
| 9 | `judge_count` | INTEGER |  |  |
| 10 | `agreement_score` | REAL (nullable) |  |  |
| 11 | `score_method` | TEXT (nullable) |  |  |
| 12 | `expected_output` | TEXT (nullable) |  |  |
| 13 | `actual_output` | TEXT (nullable) |  |  |
| 14 | `energy_uj_at_judgment` | INTEGER (nullable) |  |  |
| 15 | `manual_reviewed` | INTEGER |  |  |
| 16 | `judged_at` | TIMESTAMP |  |  |

### `output_quality_judges`

**Rows:** 0 — Judge model configurations for LLM-as-judge quality evaluation

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `judge_entry_id` | INTEGER |  |  |
| 1 | `quality_id` | INTEGER |  |  |
| 2 | `attempt_id` | INTEGER |  |  |
| 3 | `goal_id` | INTEGER |  |  |
| 4 | `judge_model` | TEXT |  |  |
| 5 | `judge_provider` | TEXT (nullable) |  |  |
| 6 | `judge_version` | TEXT (nullable) |  |  |
| 7 | `judge_temperature` | REAL (nullable) |  |  |
| 8 | `judge_score` | REAL |  |  |
| 9 | `judge_confidence` | REAL (nullable) |  |  |
| 10 | `judge_prompt_hash` | TEXT (nullable) |  |  |
| 11 | `judge_reasoning` | TEXT (nullable) |  |  |
| 12 | `judged_at` | TIMESTAMP |  |  |

### `page_configs`

**Rows:** 0 — GUI page layout configurations

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `id` | TEXT |  |  |
| 1 | `title` | TEXT |  |  |
| 2 | `slug` | TEXT (nullable) |  |  |
| 3 | `icon` | TEXT (nullable) |  |  |
| 4 | `description` | TEXT (nullable) |  |  |
| 5 | `audience` | TEXT (nullable) |  |  |
| 6 | `published` | INTEGER (nullable) |  |  |
| 7 | `sort_order` | INTEGER (nullable) |  |  |
| 8 | `created_at` | TEXT (nullable) |  |  |
| 9 | `updated_at` | TEXT (nullable) |  |  |

### `page_metric_configs`

**Rows:** 0 — Metric display configurations per GUI page

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `id` | INTEGER |  |  |
| 1 | `section_id` | INTEGER |  |  |
| 2 | `metric_id` | TEXT |  |  |
| 3 | `position` | INTEGER |  |  |
| 4 | `label_override` | TEXT (nullable) |  |  |
| 5 | `color_override` | TEXT (nullable) |  |  |
| 6 | `unit_override` | TEXT (nullable) |  |  |
| 7 | `thesis` | INTEGER (nullable) |  |  |
| 8 | `decimals` | INTEGER (nullable) |  |  |
| 9 | `active` | INTEGER (nullable) |  |  |

### `page_sections`

**Rows:** 0 — GUI page section definitions

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `id` | INTEGER |  |  |
| 1 | `page_id` | TEXT |  |  |
| 2 | `position` | INTEGER |  |  |
| 3 | `component` | TEXT |  |  |
| 4 | `title` | TEXT (nullable) |  |  |
| 5 | `cols` | INTEGER (nullable) |  |  |
| 6 | `query_id` | TEXT (nullable) |  |  |
| 7 | `props` | TEXT (nullable) |  |  |
| 8 | `visible_in` | TEXT (nullable) |  |  |
| 9 | `active` | INTEGER (nullable) |  |  |

### `page_templates`

**Rows:** 0 — GUI page template definitions

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `id` | TEXT |  |  |
| 1 | `name` | TEXT |  |  |
| 2 | `description` | TEXT (nullable) |  |  |
| 3 | `config` | TEXT (nullable) |  |  |
| 4 | `created_at` | TEXT (nullable) |  |  |
| 5 | `updated_at` | TEXT (nullable) |  |  |

### `platform_domain_relationships`

**Rows:** 0 — Platform-to-energy-domain mapping for cross-platform queries

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `hw_id` | INTEGER | SYSTEM |  |
| 1 | `hardware_hash` | TEXT |  |  |
| 2 | `source_id` | INTEGER |  |  |
| 3 | `domain_id` | INTEGER |  |  |
| 4 | `parent_domain_id` | INTEGER (nullable) |  |  |
| 5 | `contributes_to_parent` | BOOLEAN |  |  |

### `power_limit_events`

**Rows:** 0 — Thermal throttle and power limit events during runs

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `event_id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER | SYSTEM |  |
| 2 | `timestamp_ns` | INTEGER |  |  |
| 3 | `limit_id` | INTEGER |  |  |
| 4 | `old_value_mw` | REAL (nullable) |  |  |
| 5 | `new_value_mw` | REAL |  |  |

### `power_limits`

**Rows:** 4 — Platform thermal design power limits

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `limit_id` | INTEGER |  |  |
| 1 | `limit_name` | TEXT |  |  |
| 2 | `description` | TEXT (nullable) |  |  |
| 3 | `units` | TEXT |  |  |

### `power_rail_samples`

**Rows:** 275,220 — SPBM per-rail power readings (NVIDIA Grace only)

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `rail_sample_id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER | SYSTEM |  |
| 2 | `timestamp_ns` | INTEGER |  |  |
| 3 | `interval_ns` | INTEGER (nullable) |  |  |
| 4 | `rail_id` | INTEGER |  |  |
| 5 | `power_mw` | REAL |  |  |

### `power_rails`

**Rows:** 10 — SPBM power rail definitions (NVIDIA Grace only)

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `rail_id` | INTEGER |  |  |
| 1 | `rail_name` | TEXT |  |  |
| 2 | `device_type` | TEXT |  |  |
| 3 | `parent_rail_id` | INTEGER (nullable) |  |  |
| 4 | `rail_kind` | TEXT |  |  |
| 5 | `hwmon_channel` | TEXT (nullable) |  |  |
| 6 | `hw_config_key` | TEXT (nullable) |  |  |
| 7 | `notes` | TEXT (nullable) |  |  |

### `query_registry`

**Rows:** 0 — All SQL queries — no SQL hardcoded in application code

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `id` | TEXT |  |  |
| 1 | `name` | TEXT |  |  |
| 2 | `description` | TEXT (nullable) |  |  |
| 3 | `metric_type` | TEXT |  |  |
| 4 | `sql_text` | TEXT (nullable) |  |  |
| 5 | `sql_file` | TEXT (nullable) |  |  |
| 6 | `dialect_aware` | INTEGER (nullable) |  |  |
| 7 | `returns` | TEXT (nullable) |  |  |
| 8 | `depends_on` | TEXT (nullable) |  |  |
| 9 | `formula` | TEXT (nullable) |  |  |
| 10 | `endpoint_path` | TEXT (nullable) |  |  |
| 11 | `group_name` | TEXT (nullable) |  |  |
| 12 | `parameters` | TEXT (nullable) |  |  |
| 13 | `enrich_metrics` | INTEGER (nullable) |  |  |
| 14 | `cache_ttl_sec` | INTEGER (nullable) |  |  |
| 15 | `active` | INTEGER (nullable) |  |  |
| 16 | `created_at` | TEXT (nullable) |  |  |
| 17 | `updated_at` | TEXT (nullable) |  |  |
| 18 | `source_yaml` | TEXT (nullable) |  |  |
| 19 | `source_tab` | TEXT (nullable) |  |  |
| 20 | `version` | TEXT (nullable) |  |  |

### `retry_policy`

**Rows:** 3 — Retry behavior configuration per failure type

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `policy_id` | INTEGER |  |  |
| 1 | `policy_name` | TEXT |  |  |
| 2 | `max_retries` | INTEGER |  |  |
| 3 | `retry_on_timeout` | INTEGER |  |  |
| 4 | `retry_on_tool_error` | INTEGER |  |  |
| 5 | `retry_on_api_error` | INTEGER |  |  |
| 6 | `retry_on_wrong_answer` | INTEGER |  |  |
| 7 | `backoff_seconds` | REAL |  |  |
| 8 | `created_at` | TIMESTAMP (nullable) |  |  |

### `run_outliers`

**Rows:** 22 — Outlier detection results per run with classification and severity

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `outlier_id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER | SYSTEM |  |
| 2 | `metric_name` | TEXT |  |  |
| 3 | `detection_method` | TEXT |  |  |
| 4 | `detection_version` | INTEGER |  |  |
| 5 | `population_key` | TEXT |  |  |
| 6 | `population_size` | INTEGER |  |  |
| 7 | `raw_value` | REAL |  |  |
| 8 | `population_median` | REAL (nullable) |  |  |
| 9 | `population_mad` | REAL (nullable) |  |  |
| 10 | `z_score` | REAL (nullable) |  |  |
| 11 | `iqr_lower_fence` | REAL (nullable) |  |  |
| 12 | `iqr_upper_fence` | REAL (nullable) |  |  |
| 13 | `threshold_violated` | REAL |  |  |
| 14 | `direction` | TEXT (nullable) |  |  |
| 15 | `severity` | TEXT |  |  |
| 16 | `detection_status` | TEXT |  |  |
| 17 | `review_status` | TEXT |  |  |
| 18 | `reviewed_by` | TEXT (nullable) |  |  |
| 19 | `reviewed_at` | TIMESTAMP (nullable) |  |  |
| 20 | `review_note` | TEXT (nullable) |  |  |
| 21 | `detected_at` | TIMESTAMP |  |  |
| 22 | `outlier_class` | TEXT (nullable) |  |  |

### `run_power_limits`

**Rows:** 984 — Power limit state snapshots captured during runs

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `run_id` | INTEGER | SYSTEM |  |
| 1 | `limit_id` | INTEGER |  |  |
| 2 | `value_mw` | REAL |  |  |

### `run_quality`

**Rows:** 1,554 — Composite run quality scores across multiple quality dimensions

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `run_id` | INTEGER | SYSTEM |  |
| 1 | `experiment_valid` | INTEGER | SYSTEM |  |
| 2 | `quality_score` | REAL |  |  |
| 3 | `rejection_reason` | TEXT (nullable) |  |  |
| 4 | `quality_version` | INTEGER |  |  |
| 5 | `computed_at` | TIMESTAMP (nullable) |  |  |
| 6 | `gpu_valid` | INTEGER (nullable) |  |  |
| 7 | `gpu_rejection_reason` | TEXT (nullable) |  |  |

### `runs`

**Rows:** 1,554 — One row per linear or agentic workflow execution (153 columns)

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `run_id` | INTEGER | SYSTEM |  |
| 1 | `exp_id` | INTEGER | SYSTEM |  |
| 2 | `hw_id` | INTEGER (nullable) | SYSTEM | Hardware fingerprint from hw_config.json. Identifies the physical machine. |
| 3 | `baseline_id` | TEXT (nullable) | SYSTEM |  |
| 4 | `run_number` | INTEGER (nullable) | SYSTEM |  |
| 5 | `workflow_type` | TEXT | SYSTEM |  |
| 6 | `start_time_ns` | INTEGER (nullable) | MEASURED (application) | System Wall Clock |
| 7 | `end_time_ns` | INTEGER (nullable) | MEASURED (application) | System Wall Clock |
| 8 | `duration_ns` | INTEGER (nullable) | MEASURED (application) | Inference window only — first token request to last token received. |
| 9 | `total_energy_uj` | INTEGER (nullable) | CALCULATED (silicon) | Hardware counter value in µJ. Zero on modeled platforms. |
| 10 | `dynamic_energy_uj` | INTEGER (nullable) | CALCULATED (silicon) | Dynamic Energy Calculation |
| 11 | `baseline_energy_uj` | INTEGER (nullable) | CALCULATED (silicon) | Dynamic Energy Calculation |
| 12 | `avg_power_watts` | REAL (nullable) | CALCULATED (silicon) | Dynamic Energy Calculation |
| 13 | `pkg_energy_uj` | INTEGER (nullable) | MEASURED (silicon) | RAPL MSR Direct Package Energy |
| 14 | `core_energy_uj` | INTEGER (nullable) | MEASURED (silicon) | RAPL MSR Direct Package Energy |
| 15 | `uncore_energy_uj` | INTEGER (nullable) | MEASURED (silicon) | RAPL MSR Direct Package Energy |
| 16 | `dram_energy_uj` | INTEGER (nullable) | MEASURED (silicon) | RAPL MSR Direct Package Energy |
| 17 | `instructions` | BIGINT (nullable) | MEASURED (silicon) | BIGINT — modern CPUs execute billions/second, exceeds INT32 range. |
| 18 | `cycles` | BIGINT (nullable) | MEASURED (silicon) | Linux perf Hardware Counters |
| 19 | `ipc` | REAL (nullable) | CALCULATED (silicon) | Instructions Per Cycle (IPC) |
| 20 | `cache_misses` | BIGINT (nullable) | MEASURED (silicon) | Linux perf Hardware Counters |
| 21 | `cache_references` | BIGINT (nullable) | MEASURED (silicon) | Linux perf Hardware Counters |
| 22 | `cache_miss_rate` | REAL (nullable) | CALCULATED (silicon) | LLC Cache Miss Rate |
| 23 | `page_faults` | INTEGER (nullable) | MEASURED (silicon) | Linux perf Hardware Counters |
| 24 | `major_page_faults` | INTEGER (nullable) | MEASURED (silicon) | Linux perf Hardware Counters |
| 25 | `minor_page_faults` | INTEGER (nullable) | MEASURED (silicon) | Linux perf Hardware Counters |
| 26 | `context_switches_voluntary` | INTEGER (nullable) | MEASURED (silicon) | Linux perf Hardware Counters |
| 27 | `context_switches_involuntary` | INTEGER (nullable) | MEASURED (silicon) | Linux perf Hardware Counters |
| 28 | `total_context_switches` | INTEGER (nullable) | MEASURED (silicon) | Linux perf Hardware Counters |
| 29 | `thread_migrations` | INTEGER (nullable) | MEASURED (silicon) | Linux perf Hardware Counters |
| 30 | `run_queue_length` | REAL (nullable) | MEASURED (os) | OS Scheduler Statistics Reader |
| 31 | `kernel_time_ms` | REAL (nullable) | MEASURED (os) | OS Scheduler Statistics Reader |
| 32 | `user_time_ms` | REAL (nullable) | MEASURED (os) | OS Scheduler Statistics Reader |
| 33 | `frequency_mhz` | REAL (nullable) | MEASURED (silicon) | Intel Turbostat CPU Frequency Reader |
| 34 | `ring_bus_freq_mhz` | REAL (nullable) | MEASURED (silicon) | Intel Turbostat CPU Frequency Reader |
| 35 | `cpu_busy_mhz` | REAL (nullable) | MEASURED (silicon) | Intel Turbostat CPU Frequency Reader |
| 36 | `cpu_avg_mhz` | REAL (nullable) | MEASURED (silicon) | Intel Turbostat CPU Frequency Reader |
| 37 | `package_temp_celsius` | REAL (nullable) | MEASURED (silicon) | Linux sysfs Thermal Sensor |
| 38 | `baseline_temp_celsius` | REAL (nullable) | MEASURED (silicon) | Linux sysfs Thermal Sensor |
| 39 | `start_temp_c` | REAL (nullable) | MEASURED (silicon) | Linux sysfs Thermal Sensor |
| 40 | `max_temp_c` | REAL (nullable) | MEASURED (silicon) | Linux sysfs Thermal Sensor |
| 41 | `min_temp_c` | REAL (nullable) | MEASURED (silicon) | Linux sysfs Thermal Sensor |
| 42 | `thermal_delta_c` | REAL (nullable) | MEASURED (silicon) | Linux sysfs Thermal Sensor |
| 43 | `thermal_during_experiment` | BOOLEAN (nullable) | MEASURED (silicon) | Linux sysfs Thermal Sensor |
| 44 | `thermal_now_active` | BOOLEAN (nullable) | MEASURED (silicon) | Linux sysfs Thermal Sensor |
| 45 | `thermal_since_boot` | BOOLEAN (nullable) | MEASURED (silicon) | Linux sysfs Thermal Sensor |
| 46 | `experiment_valid` | BOOLEAN (nullable) | SYSTEM |  |
| 47 | `c2_time_seconds` | REAL (nullable) | MEASURED (silicon) | MSR C-State Register Reader |
| 48 | `c3_time_seconds` | REAL (nullable) | MEASURED (silicon) | MSR C-State Register Reader |
| 49 | `c6_time_seconds` | REAL (nullable) | MEASURED (silicon) | MSR C-State Register Reader |
| 50 | `c7_time_seconds` | REAL (nullable) | MEASURED (silicon) | MSR C-State Register Reader |
| 51 | `swap_total_mb` | REAL (nullable) | MEASURED (os) | Linux swap partition/file size — virtual memory overflow from RAM to disk. |
| 52 | `swap_end_free_mb` | REAL (nullable) | MEASURED (os) | OS Memory Statistics Reader |
| 53 | `swap_start_used_mb` | REAL (nullable) | MEASURED (os) | OS Memory Statistics Reader |
| 54 | `swap_end_used_mb` | REAL (nullable) | MEASURED (os) | OS Memory Statistics Reader |
| 55 | `swap_start_cached_mb` | REAL (nullable) | MEASURED (os) | OS Memory Statistics Reader |
| 56 | `swap_end_cached_mb` | REAL (nullable) | MEASURED (os) | OS Memory Statistics Reader |
| 57 | `swap_end_percent` | REAL (nullable) | MEASURED (os) | OS Memory Statistics Reader |
| 58 | `wakeup_latency_us` | REAL (nullable) | MEASURED (os) | OS Scheduler Statistics Reader |
| 59 | `interrupt_rate` | REAL (nullable) | MEASURED (os) | OS Scheduler Statistics Reader |
| 60 | `thermal_throttle_flag` | INTEGER (nullable) | MEASURED (silicon) | Linux sysfs Thermal Sensor |
| 61 | `rss_memory_mb` | REAL (nullable) | MEASURED (os) | OS Memory Statistics Reader |
| 62 | `vms_memory_mb` | REAL (nullable) | MEASURED (os) | OS Memory Statistics Reader |
| 63 | `total_tokens` | INTEGER (nullable) | MEASURED (application) | TTFT / TPOT Wall Clock Measurement |
| 64 | `prompt_tokens` | INTEGER (nullable) | MEASURED (application) | TTFT / TPOT Wall Clock Measurement |
| 65 | `completion_tokens` | INTEGER (nullable) | MEASURED (application) | TTFT / TPOT Wall Clock Measurement |
| 66 | `dns_latency_ms` | REAL (nullable) |  |  |
| 67 | `api_latency_ms` | REAL (nullable) | MEASURED (application) | TTFT / TPOT Wall Clock Measurement |
| 68 | `compute_time_ms` | REAL (nullable) |  |  |
| 69 | `bytes_sent` | INTEGER (nullable) | MEASURED (os) | Network I/O Measurement |
| 70 | `bytes_recv` | INTEGER (nullable) | MEASURED (os) | Network I/O Measurement |
| 71 | `tcp_retransmits` | INTEGER (nullable) | MEASURED (os) | Network I/O Measurement |
| 72 | `governor` | TEXT (nullable) | SYSTEM |  |
| 73 | `turbo_enabled` | BOOLEAN (nullable) | SYSTEM |  |
| 74 | `is_cold_start` | BOOLEAN (nullable) | SYSTEM |  |
| 75 | `background_cpu_percent` | REAL (nullable) | MEASURED (os) | OS Scheduler Statistics Reader |
| 76 | `process_count` | INTEGER (nullable) | MEASURED (os) | OS Scheduler Statistics Reader |
| 77 | `planning_time_ms` | REAL (nullable) | CALCULATED (orchestration) | Orchestration Tax Calculation |
| 78 | `execution_time_ms` | REAL (nullable) | CALCULATED (orchestration) | Orchestration Tax Calculation |
| 79 | `synthesis_time_ms` | REAL (nullable) | CALCULATED (orchestration) | Orchestration Tax Calculation |
| 80 | `phase_planning_ratio` | REAL (nullable) | CALCULATED (orchestration) | Orchestration Tax Calculation |
| 81 | `phase_execution_ratio` | REAL (nullable) | CALCULATED (orchestration) | Orchestration Tax Calculation |
| 82 | `phase_synthesis_ratio` | REAL (nullable) | CALCULATED (orchestration) | Orchestration Tax Calculation |
| 83 | `llm_calls` | INTEGER (nullable) | SYSTEM |  |
| 84 | `tool_calls` | INTEGER (nullable) | SYSTEM |  |
| 85 | `tools_used` | INTEGER (nullable) | SYSTEM |  |
| 86 | `steps` | INTEGER (nullable) | SYSTEM |  |
| 87 | `avg_step_time_ms` | REAL (nullable) | CALCULATED (orchestration) | Orchestration Tax Calculation |
| 88 | `orchestration_cpu_ms` | REAL (nullable) | CALCULATED (orchestration) | Orchestration Tax Calculation |
| 89 | `complexity_level` | INTEGER (nullable) | CALCULATED (orchestration) | Orchestration Complexity Score |
| 90 | `complexity_score` | REAL (nullable) | CALCULATED (orchestration) | 0.4·(llm_calls/10) + 0.3·(tool_calls/10) + 0.3·(tokens/1000), capped at 1.0 |
| 91 | `carbon_g` | REAL (nullable) | INFERRED (application) | Calculated from energy × regional grid intensity. Not directly measured. |
| 92 | `water_ml` | REAL (nullable) | INFERRED (application) | Calculated from energy × PUE × WUE factors. Not directly measured. |
| 93 | `methane_mg` | REAL (nullable) | INFERRED (application) | Calculated from carbon_g using IPCC AR6 GWP factors. Not measured. |
| 94 | `energy_per_instruction` | REAL (nullable) | CALCULATED (application) | Energy Efficiency Metrics |
| 95 | `energy_per_cycle` | REAL (nullable) | CALCULATED (application) | Energy Efficiency Metrics |
| 96 | `energy_per_token` | REAL (nullable) | CALCULATED (application) | Energy Efficiency Metrics |
| 97 | `instructions_per_token` | REAL (nullable) | CALCULATED (application) | Energy Efficiency Metrics |
| 98 | `interrupts_per_second` | REAL (nullable) | MEASURED (os) | OS Scheduler Statistics Reader |
| 99 | `run_state_hash` | TEXT (nullable) | SYSTEM |  |
| 100 | `pid` | INTEGER (nullable) | SYSTEM |  |
| 101 | `cpu_fraction` | REAL (nullable) | CALCULATED (os) | CPU Fraction-Based Energy Attribution |
| 102 | `attributed_energy_uj` | INTEGER (nullable) | CALCULATED (os) | total_energy_uj minus idle baseline × duration. NULL until ETL runs. |
| 103 | `energy_measurement_mode` | TEXT (nullable) | MEASURED | direct (hardware counter) / modeled (no counter) / unavailable |
| 104 | `planning_energy_uj` | INTEGER (nullable) | MEASURED (orchestration) | Phase Attribution v2 (Direct RAPL Sample Measurement) |
| 105 | `execution_energy_uj` | INTEGER (nullable) | MEASURED (orchestration) | Phase Attribution v2 (Direct RAPL Sample Measurement) |
| 106 | `synthesis_energy_uj` | INTEGER (nullable) | MEASURED (orchestration) | Phase Attribution v2 (Direct RAPL Sample Measurement) |
| 107 | `inter_phase_energy_uj` | INTEGER (nullable) | MEASURED (orchestration) | Phase Attribution v2 (Direct RAPL Sample Measurement) |
| 108 | `phase_sample_coverage_pct` | REAL (nullable) | MEASURED (orchestration) | Phase Attribution v2 (Direct RAPL Sample Measurement) |
| 109 | `l1d_cache_misses_total` | BIGINT (nullable) | MEASURED (silicon) | Perf Cache Counters |
| 110 | `l2_cache_misses_total` | BIGINT (nullable) | MEASURED (silicon) | Perf Cache Counters |
| 111 | `l3_cache_hits_total` | BIGINT (nullable) | MEASURED (silicon) | Perf Cache Counters |
| 112 | `l3_cache_misses_total` | BIGINT (nullable) | MEASURED (silicon) | Perf Cache Counters |
| 113 | `disk_read_bytes_total` | BIGINT (nullable) | MEASURED (os) | Disk I/O Statistics |
| 114 | `disk_write_bytes_total` | BIGINT (nullable) | MEASURED (os) | Disk I/O Statistics |
| 115 | `voltage_vcore_avg` | REAL (nullable) | MEASURED |  |
| 116 | `task_duration_ns` | INTEGER (nullable) | MEASURED (os) | Task vs Framework Duration Boundary |
| 117 | `framework_overhead_ns` | INTEGER (nullable) | MEASURED (os) | Task vs Framework Duration Boundary |
| 118 | `total_run_duration_ns` | INTEGER (nullable) | MEASURED (os) | Full experiment duration including warmup, cooldown, and overhead. |
| 119 | `duration_includes_overhead` | INTEGER (nullable) | SYSTEM |  |
| 120 | `energy_sample_coverage_pct` | REAL (nullable) | CALCULATED (os) | % of run duration covered by energy samples. <80% = sampling gap. |
| 121 | `avg_task_power_watts` | REAL (nullable) | MEASURED (os) | Task vs Framework Duration Boundary |
| 122 | `rapl_before_pretask_uj` | INTEGER (nullable) | MEASURED (os) | Task vs Framework Duration Boundary |
| 123 | `pre_task_energy_uj` | INTEGER (nullable) | MEASURED (os) | Task vs Framework Duration Boundary |
| 124 | `pre_task_duration_ns` | INTEGER (nullable) | MEASURED (os) | Task vs Framework Duration Boundary |
| 125 | `rapl_after_task_uj` | INTEGER (nullable) | MEASURED (os) | Task vs Framework Duration Boundary |
| 126 | `post_task_energy_uj` | INTEGER (nullable) | MEASURED (os) | Task vs Framework Duration Boundary |
| 127 | `post_task_duration_ns` | INTEGER (nullable) | MEASURED (os) | Task vs Framework Duration Boundary |
| 128 | `framework_overhead_energy_uj` | INTEGER (nullable) | MEASURED (os) | Task vs Framework Duration Boundary |
| 129 | `gpu_total_energy_uj` | INTEGER (nullable) | MEASURED (silicon) | Intel Iris Xe GPU Energy via MSR 0x641 |
| 130 | `gpu_baseline_energy_uj` | INTEGER (nullable) | CALCULATED (silicon) | GPU Dynamic Energy via Baseline Subtraction |
| 131 | `gpu_dynamic_energy_uj` | INTEGER (nullable) | CALCULATED (silicon) | GPU Dynamic Energy via Run-Local Adaptive Idle Baseline |
| 132 | `gpu_pct_of_pkg` | REAL (nullable) | CALCULATED (silicon) | GPU Dynamic Energy via Run-Local Adaptive Idle Baseline |
| 133 | `gpu_attribution_method` | TEXT (nullable) | CALCULATED (application) | GPU Dynamic Energy Attribution (Exclusive Workload) |
| 134 | `gpu_count` | INTEGER (nullable) | MEASURED (silicon) | GPU Idle Baseline (2-Sigma Method) |
| 135 | `ttft_ms` | REAL (nullable) | MEASURED (application) | Time to First Token Measurement |
| 136 | `tpot_ms` | REAL (nullable) | MEASURED (application) | Time Per Output Token Measurement |
| 137 | `global_run_id` | TEXT (nullable) | SYSTEM |  |
| 138 | `sync_status` | INTEGER | SYSTEM |  |
| 139 | `sync_samples_status` | INTEGER | SYSTEM |  |
| 140 | `gpu_dynamic_method` | TEXT (nullable) |  |  |
| 141 | `gpu_idle_power_w_used` | REAL (nullable) |  |  |
| 142 | `gpu_spbm_total_uj` | INTEGER (nullable) |  |  |
| 143 | `gpu_spbm_dynamic_uj` | INTEGER (nullable) |  |  |
| 144 | `gpu_residual_dynamic_uj` | INTEGER (nullable) |  |  |
| 145 | `spbm_power_sampling_freq_hz` | REAL (nullable) |  |  |
| 146 | `spbm_samples_expected` | INTEGER (nullable) |  |  |
| 147 | `spbm_samples_observed` | INTEGER (nullable) |  |  |
| 148 | `spbm_sample_coverage_pct` | REAL (nullable) |  |  |
| 149 | `spbm_integration_method` | TEXT (nullable) |  |  |
| 150 | `spbm_conversion_loss_uj` | INTEGER (nullable) |  |  |
| 151 | `spbm_conversion_efficiency` | REAL (nullable) |  |  |
| 152 | `cpu_active_ratio` | REAL (nullable) | MEASURED (os) | CPU Active Ratio (cross-platform) |

### `schema_version`

**Rows:** 23 — Current schema version tracking

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `version` | INT (nullable) |  |  |
| 1 | `applied_at` | NUM (nullable) |  |  |
| 2 | `description` | TEXT (nullable) |  |  |

### `sqlite_sequence`

**Rows:** 33 — SQLite internal auto-increment sequence tracking

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `name` | TEXT (nullable) |  |  |
| 1 | `seq` | TEXT (nullable) |  |  |

### `standardization_registry`

**Rows:** 0 — Metric standardization parameters for cross-platform normalization

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `id` | INTEGER |  |  |
| 1 | `standard_id` | TEXT |  |  |
| 2 | `category` | TEXT (nullable) |  |  |
| 3 | `value` | REAL |  |  |
| 4 | `unit` | TEXT (nullable) |  |  |
| 5 | `source` | TEXT (nullable) |  |  |
| 6 | `source_url` | TEXT (nullable) |  |  |
| 7 | `valid_from` | TEXT (nullable) |  |  |
| 8 | `valid_until` | TEXT (nullable) |  |  |
| 9 | `version` | INTEGER (nullable) |  |  |
| 10 | `notes` | TEXT (nullable) |  |  |
| 11 | `created_at` | TEXT (nullable) |  |  |

### `task_categories`

**Rows:** 65 — Task definitions loaded from config/tasks.yaml

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `task_id` | TEXT |  |  |
| 1 | `category` | TEXT |  |  |

### `task_quality_config`

**Rows:** 0 — Quality thresholds per task type

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `config_id` | INTEGER |  |  |
| 1 | `task_category` | TEXT |  |  |
| 2 | `metric_type` | TEXT |  |  |
| 3 | `judge_method` | TEXT |  |  |
| 4 | `threshold` | REAL |  |  |
| 5 | `dual_judge` | INTEGER |  |  |
| 6 | `created_at` | TIMESTAMP (nullable) |  |  |

### `task_retry_override`

**Rows:** 0 — Per-task retry policy overrides

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `task_category` | TEXT |  |  |
| 1 | `max_retries` | INTEGER |  |  |
| 2 | `policy_name` | TEXT |  |  |

### `thermal_samples`

**Rows:** 34,904 — Temperature, fan RPM, voltage at 1Hz

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `sample_id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER | SYSTEM |  |
| 2 | `timestamp_ns` | INTEGER |  |  |
| 3 | `sample_time_s` | REAL (nullable) |  |  |
| 4 | `cpu_temp` | REAL (nullable) |  |  |
| 5 | `system_temp` | REAL (nullable) |  |  |
| 6 | `wifi_temp` | REAL (nullable) |  |  |
| 7 | `throttle_event` | INTEGER (nullable) |  |  |
| 8 | `voltage_vcore` | REAL (nullable) |  |  |
| 9 | `fan_rpm` | INTEGER (nullable) |  |  |
| 10 | `all_zones_json` | TEXT (nullable) |  |  |
| 11 | `sensor_count` | INTEGER (nullable) |  |  |
| 12 | `sample_start_ns` | INTEGER (nullable) |  |  |
| 13 | `sample_end_ns` | INTEGER (nullable) |  |  |
| 14 | `interval_ns` | INTEGER (nullable) |  |  |
| 15 | `global_run_id` | TEXT (nullable) | SYSTEM |  |

### `thermal_samples_v2`

**Rows:** 29,778 — Thermal samples schema v2 with zone registry integration

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER | SYSTEM |  |
| 2 | `zone_id` | INTEGER |  |  |
| 3 | `timestamp_ns` | INTEGER |  |  |
| 4 | `temp_celsius` | REAL | MEASURED (os) | Normalized Per-Zone Thermal Reader V2 |
| 5 | `quality_flag` | TEXT | MEASURED (os) | Normalized Per-Zone Thermal Reader V2 |
| 6 | `invalid_reason` | TEXT (nullable) |  |  |
| 7 | `global_run_id` | TEXT (nullable) | SYSTEM |  |

### `thermal_zones`

**Rows:** 7 — Thermal zone inventory and configuration per platform

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `zone_id` | INTEGER |  |  |
| 1 | `machine_id` | TEXT |  |  |
| 2 | `zone_type` | TEXT |  |  |
| 3 | `zone_index` | INTEGER |  |  |
| 4 | `driver` | TEXT (nullable) |  |  |
| 5 | `device` | TEXT (nullable) |  |  |
| 6 | `canonical_role` | TEXT | MEASURED (os) | Normalized Per-Zone Thermal Reader V2 |
| 7 | `source_subsystem` | TEXT |  |  |
| 8 | `first_seen` | TEXT |  |  |
| 9 | `last_seen` | TEXT |  |  |
| 10 | `active` | INTEGER |  |  |

### `tool_failure_events`

**Rows:** 287 — Tool execution failure events with classification and recovery data

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `failure_id` | INTEGER |  |  |
| 1 | `attempt_id` | INTEGER |  |  |
| 2 | `goal_id` | INTEGER |  |  |
| 3 | `orchestration_event_id` | INTEGER (nullable) |  |  |
| 4 | `tool_name` | TEXT |  |  |
| 5 | `failure_type` | TEXT |  |  |
| 6 | `failure_phase` | TEXT (nullable) |  |  |
| 7 | `error_message` | TEXT (nullable) |  |  |
| 8 | `retry_attempted` | INTEGER |  |  |
| 9 | `retry_success` | INTEGER |  |  |
| 10 | `recovery_strategy` | TEXT (nullable) |  |  |
| 11 | `wasted_energy_uj` | REAL (nullable) |  |  |
| 12 | `created_at` | TIMESTAMP |  |  |

---

_Generated 2026-09-12 18:26 from schema version recorded in `migration_history`._
