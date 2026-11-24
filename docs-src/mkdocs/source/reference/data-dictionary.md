# Data Dictionary

Auto-generated from the live database schema on 2026-11-24. Run `bash scripts/build-docs.sh` to regenerate after any schema migration.

**122 tables · 37 views · 1684 columns**

Column provenance is sourced from `core/utils/provenance.py` (`COLUMN_PROVENANCE`) and `measurement_method_registry`. Columns without a provenance entry are marked as unregistered.

---

## Tables

| Table | Rows | Description |
|---|---|---|
| [`analysis_domain_config`](#analysis_domain_config) | 10 | Analysis domain definitions for ETL and reporting |
| [`analysis_view_config`](#analysis_view_config) | 8 | View configuration for the clean/measured view system |
| [`attribution_residual`](#attribution_residual) | 0 |  |
| [`audit_log`](#audit_log) | 0 | Run-level audit events — tracks metric updates and data quality changes |
| [`cache_state_snapshots`](#cache_state_snapshots) | 0 |  |
| [`component_registry`](#component_registry) | 0 | Registered system components with schema and data shape definitions |
| [`cooling_devices`](#cooling_devices) | 0 | Cooling device inventory per platform (fans, liquid coolers) |
| [`cooling_samples`](#cooling_samples) | 0 | Cooling device state and target temperature at 1Hz |
| [`cpu_idle_states`](#cpu_idle_states) | 0 | CPU C-state residency data per platform |
| [`cpu_samples`](#cpu_samples) | 0 | CPU frequency, IPC, cache counters at 10Hz |
| [`device_telemetry`](#device_telemetry) | 0 | Generic device telemetry samples for non-standard hardware |
| [`ear_decision_log`](#ear_decision_log) | 0 |  |
| [`ear_policy`](#ear_policy) | 4 |  |
| [`ear_policy_rules`](#ear_policy_rules) | 56 |  |
| [`energy_attribution`](#energy_attribution) | 0 | Phase-attributed energy values per run (planning, tool, synthesis) |
| [`energy_derived_metrics`](#energy_derived_metrics) | 0 | Computed energy metrics derived from raw samples via ETL |
| [`energy_domains`](#energy_domains) | 29 | Energy domain taxonomy (pkg, core, uncore, dram, gpu) |
| [`energy_sample_domains`](#energy_sample_domains) | 0 | Domain assignments for energy samples (pkg, core, uncore, dram, gpu) |
| [`energy_samples`](#energy_samples) | 0 | Raw RAPL/SPBM/IOKit counter reads at 100Hz |
| [`energy_samples_v2`](#energy_samples_v2) | 0 | Energy samples schema v2 with domain registry integration |
| [`energy_sources`](#energy_sources) | 9 | Energy source registry (rapl, spbm, iokit, dcgm, arm_pmu) |
| [`energy_validation_dcgm_coverage`](#energy_validation_dcgm_coverage) | 0 |  |
| [`energy_validation_engine_provenance`](#energy_validation_engine_provenance) | 0 |  |
| [`energy_validation_gpu_mem_traffic_runs`](#energy_validation_gpu_mem_traffic_runs) | 0 |  |
| [`energy_validation_gpu_mem_traffic_samples`](#energy_validation_gpu_mem_traffic_samples) | 0 |  |
| [`energy_validation_seq_attempt_runs`](#energy_validation_seq_attempt_runs) | 0 |  |
| [`energy_validation_seq_attempts`](#energy_validation_seq_attempts) | 0 |  |
| [`energy_validation_seq_bracket_eligibility`](#energy_validation_seq_bracket_eligibility) | 0 |  |
| [`energy_validation_seq_sessions`](#energy_validation_seq_sessions) | 0 |  |
| [`energy_validation_seq_step_results`](#energy_validation_seq_step_results) | 0 |  |
| [`energy_validation_seq_steps`](#energy_validation_seq_steps) | 0 |  |
| [`energy_validation_server_age`](#energy_validation_server_age) | 0 |  |
| [`environment_config`](#environment_config) | 1 | Software environment fingerprint — git commit, package versions |
| [`etl_queue`](#etl_queue) | 0 | Pending and completed ETL job tracking |
| [`eval_criteria`](#eval_criteria) | 10 | Evaluation criteria definitions for task quality assessment |
| [`experiments`](#experiments) | 0 | One experiment per research question — parent of runs |
| [`extension_registry`](#extension_registry) | 11 |  |
| [`failure_cost_profile`](#failure_cost_profile) | 0 |  |
| [`failure_injection_log`](#failure_injection_log) | 0 |  |
| [`failure_taxonomy`](#failure_taxonomy) | 19 |  |
| [`goal_attempt`](#goal_attempt) | 0 | Individual goal attempt records within goal execution sessions |
| [`goal_execution`](#goal_execution) | 0 | Goal execution session records — multi-step agentic task tracking |
| [`goal_output`](#goal_output) | 0 |  |
| [`gpu_config`](#gpu_config) | 1 | GPU configuration snapshot captured at experiment time |
| [`gpu_samples`](#gpu_samples) | 0 | DCGM GPU utilization and memory (NVIDIA Grace only) |
| [`hallucination_events`](#hallucination_events) | 0 | Detected hallucination events with classification and energy cost |
| [`hardware_config`](#hardware_config) | 1 | hw_config.json snapshot captured at experiment time |
| [`idle_baseline_domains`](#idle_baseline_domains) | 0 | Per-domain idle baseline power values (pkg, core, uncore, dram, gpu) |
| [`idle_baselines`](#idle_baselines) | 0 | Idle energy baseline measurements per platform |
| [`interrupt_samples`](#interrupt_samples) | 0 | Context switches and interrupt ticks at 10Hz |
| [`io_samples`](#io_samples) | 0 | Disk read/write byte deltas at 10Hz |
| [`llm_interactions`](#llm_interactions) | 0 | One row per LLM API call within a run |
| [`machine_setup_history`](#machine_setup_history) | 1 | Machine provisioning and configuration change history |
| [`measurement_method_registry`](#measurement_method_registry) | 102 | Master registry of all measurement methods with formula and provenance |
| [`measurement_methodology`](#measurement_methodology) | 0 | Per-run methodology audit trail — links runs to method_registry entries |
| [`method_references`](#method_references) | 128 | Literature citations per measurement method |
| [`metric_analysis_domains`](#metric_analysis_domains) | 132 | Analysis domain assignments for metrics in the view system |
| [`metric_display_registry`](#metric_display_registry) | 230 | Display configuration for all metrics in GUI and reports |
| [`migration_history`](#migration_history) | 82 | Applied migration versions with checksums |
| [`network_energy_attribution`](#network_energy_attribution) | 0 | Network wait energy attribution per run and phase |
| [`nic_samples`](#nic_samples) | 0 | Network interface byte and packet counters at 10Hz |
| [`normalization_factors`](#normalization_factors) | 0 | Grid intensity and environmental conversion factors per country |
| [`orchestration_events`](#orchestration_events) | 0 | Timeline of agentic orchestration phase transitions |
| [`orchestration_tax_summary`](#orchestration_tax_summary) | 0 | Pre-computed orchestration tax summary per experiment |
| [`outlier_detection_config`](#outlier_detection_config) | 11 | Outlier detection thresholds and domain rules |
| [`output_quality`](#output_quality) | 0 | LLM output quality scores per run |
| [`output_quality_judges`](#output_quality_judges) | 0 | Judge model configurations for LLM-as-judge quality evaluation |
| [`page_configs`](#page_configs) | 10 | GUI page layout configurations |
| [`page_metric_configs`](#page_metric_configs) | 0 | Metric display configurations per GUI page |
| [`page_sections`](#page_sections) | 7 | GUI page section definitions |
| [`page_templates`](#page_templates) | 0 | GUI page template definitions |
| [`platform_domain_relationships`](#platform_domain_relationships) | 0 | Platform-to-energy-domain mapping for cross-platform queries |
| [`policy_comparison_results`](#policy_comparison_results) | 0 |  |
| [`power_limit_events`](#power_limit_events) | 0 | Thermal throttle and power limit events during runs |
| [`power_limits`](#power_limits) | 4 | Platform thermal design power limits |
| [`power_rail_samples`](#power_rail_samples) | 0 | SPBM per-rail power readings (NVIDIA Grace only) |
| [`power_rails`](#power_rails) | 10 | SPBM power rail definitions (NVIDIA Grace only) |
| [`query_registry`](#query_registry) | 31 | All SQL queries — no SQL hardcoded in application code |
| [`recovery_events`](#recovery_events) | 0 |  |
| [`recovery_taxonomy`](#recovery_taxonomy) | 9 |  |
| [`retry_policy`](#retry_policy) | 3 | Retry behavior configuration per failure type |
| [`run_outliers`](#run_outliers) | 0 | Outlier detection results per run with classification and severity |
| [`run_power_limits`](#run_power_limits) | 0 | Power limit state snapshots captured during runs |
| [`run_quality`](#run_quality) | 0 | Composite run quality scores across multiple quality dimensions |
| [`runs`](#runs) | 0 | One row per linear or agentic workflow execution (153 columns) |
| [`sampler_stats`](#sampler_stats) | 0 |  |
| [`sandbox_identity`](#sandbox_identity) | 0 |  |
| [`schema_namespace_tables`](#schema_namespace_tables) | 57 |  |
| [`schema_version`](#schema_version) | 7 | Current schema version tracking |
| [`serving_runtime_snapshots`](#serving_runtime_snapshots) | 0 |  |
| [`span_annotations`](#span_annotations) | 0 |  |
| [`span_attributes`](#span_attributes) | 0 |  |
| [`span_events`](#span_events) | 0 |  |
| [`span_links`](#span_links) | 0 |  |
| [`span_placements`](#span_placements) | 0 |  |
| [`spans`](#spans) | 0 |  |
| [`sqlite_sequence`](#sqlite_sequence) | 17 | SQLite internal auto-increment sequence tracking |
| [`stage_event`](#stage_event) | 0 |  |
| [`stage_graph`](#stage_graph) | 0 |  |
| [`standardization_registry`](#standardization_registry) | 0 | Metric standardization parameters for cross-platform normalization |
| [`state_reuse_events`](#state_reuse_events) | 0 |  |
| [`state_reuse_taxonomy`](#state_reuse_taxonomy) | 6 |  |
| [`task_categories`](#task_categories) | 0 | Task definitions loaded from config/tasks.yaml |
| [`task_quality_config`](#task_quality_config) | 17 | Quality thresholds per task type |
| [`task_retry_override`](#task_retry_override) | 0 | Per-task retry policy overrides |
| [`te_ambient_samples`](#te_ambient_samples) | 0 |  |
| [`te_channel`](#te_channel) | 0 |  |
| [`te_clock_anchor`](#te_clock_anchor) | 0 |  |
| [`te_cooling_samples`](#te_cooling_samples) | 0 |  |
| [`te_gpu_temp_samples`](#te_gpu_temp_samples) | 0 |  |
| [`te_idle_window`](#te_idle_window) | 0 |  |
| [`te_power_rail_samples`](#te_power_rail_samples) | 0 |  |
| [`te_sensor`](#te_sensor) | 0 |  |
| [`te_session`](#te_session) | 0 |  |
| [`te_session_check`](#te_session_check) | 0 |  |
| [`te_thermal_samples`](#te_thermal_samples) | 0 |  |
| [`thermal_samples`](#thermal_samples) | 0 | Temperature, fan RPM, voltage at 1Hz |
| [`thermal_samples_v2`](#thermal_samples_v2) | 0 | Thermal samples schema v2 with zone registry integration |
| [`thermal_zones`](#thermal_zones) | 0 | Thermal zone inventory and configuration per platform |
| [`tool_failure_events`](#tool_failure_events) | 0 | Tool execution failure events with classification and recovery data |
| [`tool_selection_events`](#tool_selection_events) | 0 |  |
| [`writer_idempotency`](#writer_idempotency) | 0 |  |

---

## Views

A-LEMS maintains a set of filtered views for analysis. Views prefixed `v_runs_clean_` exclude confirmed outliers. Views prefixed `v_runs_measured_` exclude data quality failures but retain statistical anomalies for distribution analysis.

| View | Purpose |
|---|---|
| `energy_samples_with_power` |  |
| `ml_features` |  |
| `orchestration_analysis` |  |
| `research_metrics_view` |  |
| `te_v_recording_thermal` |  |
| `v_attribution_summary` |  |
| `v_energy` |  |
| `v_energy_normalized` |  |
| `v_failure_cost_by_experiment` |  |
| `v_failure_cost_comparison` |  |
| `v_failure_energy_taxonomy` |  |
| `v_fraction_verification` |  |
| `v_goal_energy_decomposition` |  |
| `v_idle_baseline_domains` |  |
| `v_idle_baselines` |  |
| `v_outcome_efficiency` |  |
| `v_platform_baseline_summary` |  |
| `v_quality_energy_frontier` |  |
| `v_recovery_cost_by_depth` |  |
| `v_runs_clean` | Excludes confirmed outliers of any class |
| `v_runs_clean_cpu` | Excludes confirmed outliers of any class |
| `v_runs_clean_energy` | Excludes confirmed outliers of any class |
| `v_runs_clean_llm` | Excludes confirmed outliers of any class |
| `v_runs_clean_orchestration` | Excludes confirmed outliers of any class |
| `v_runs_clean_system` | Excludes confirmed outliers of any class |
| `v_runs_clean_thermal` | Excludes confirmed outliers of any class |
| `v_runs_goal` |  |
| `v_runs_measured_cpu` | Excludes data quality failures, retains statistical anomalies |
| `v_runs_measured_energy` | Excludes data quality failures, retains statistical anomalies |
| `v_runs_measured_llm` | Excludes data quality failures, retains statistical anomalies |
| `v_runs_measured_orchestration` | Excludes data quality failures, retains statistical anomalies |
| `v_runs_measured_system` | Excludes data quality failures, retains statistical anomalies |
| `v_runs_measured_thermal` | Excludes data quality failures, retains statistical anomalies |
| `v_runs_unfiltered` | All runs including confirmed outliers |
| `v_state_reuse_impact` |  |
| `v_thermal_cpu` |  |
| `v_wasted_energy_per_success` |  |

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

### `attribution_residual`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `residual_id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER | SYSTEM |  |
| 2 | `domain` | TEXT |  |  |
| 3 | `window_label` | TEXT |  |  |
| 4 | `measured_uj` | BIGINT (nullable) |  |  |
| 5 | `attributed_uj` | BIGINT (nullable) |  |  |
| 6 | `residual_uj` | BIGINT (nullable) |  |  |
| 7 | `tolerance_uj` | BIGINT (nullable) |  |  |
| 8 | `status` | TEXT (nullable) |  |  |
| 9 | `created_at` | TIMESTAMP |  |  |

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

### `cache_state_snapshots`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `snapshot_id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER (nullable) | SYSTEM |  |
| 2 | `attempt_id` | INTEGER (nullable) |  |  |
| 3 | `timestamp_ns` | INTEGER (nullable) |  |  |
| 4 | `engine_name` | TEXT (nullable) |  |  |
| 5 | `cache_type` | TEXT (nullable) |  |  |
| 6 | `capacity_tokens` | INTEGER (nullable) |  |  |
| 7 | `occupied_tokens` | INTEGER (nullable) |  |  |
| 8 | `occupancy_fraction` | REAL (nullable) |  |  |
| 9 | `hit_rate_aggregate` | REAL (nullable) |  |  |
| 10 | `created_at` | TIMESTAMP (nullable) |  |  |

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

**Rows:** 0 — Cooling device inventory per platform (fans, liquid coolers)

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `device_id` | INTEGER |  |  |
| 1 | `machine_id` | TEXT |  |  |
| 2 | `device_type` | TEXT |  |  |
| 3 | `device_index` | INTEGER |  |  |
| 4 | `driver` | TEXT (nullable) |  |  |
| 5 | `device` | TEXT (nullable) |  |  |
| 6 | `canonical_role` | TEXT | MEASURED (os) | [Normalized Per-Zone Thermal Reader V2](../research/thermal-subsystem.md#thermal-zone-sysfs-v2) |
| 7 | `source_subsystem` | TEXT |  |  |
| 8 | `max_state` | INTEGER |  |  |
| 9 | `first_seen` | TEXT |  |  |
| 10 | `last_seen` | TEXT |  |  |
| 11 | `active` | INTEGER |  |  |

### `cooling_samples`

**Rows:** 0 — Cooling device state and target temperature at 1Hz

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER | SYSTEM |  |
| 2 | `device_id` | INTEGER |  |  |
| 3 | `timestamp_ns` | INTEGER |  |  |
| 4 | `cur_state` | INTEGER | MEASURED (os) | [Cooling Device State Reader V1](../research/thermal-subsystem.md#cooling-sysfs-v1) |
| 5 | `quality_flag` | TEXT | MEASURED (os) | [Normalized Per-Zone Thermal Reader V2](../research/thermal-subsystem.md#thermal-zone-sysfs-v2) |
| 6 | `invalid_reason` | TEXT (nullable) |  |  |
| 7 | `global_run_id` | TEXT (nullable) | SYSTEM |  |

### `cpu_idle_states`

**Rows:** 0 — CPU C-state residency data per platform

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

**Rows:** 0 — CPU frequency, IPC, cache counters at 10Hz

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `sample_id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER | SYSTEM |  |
| 2 | `timestamp_ns` | INTEGER |  |  |
| 3 | `cpu_util_percent` | REAL (nullable) |  |  |
| 4 | `cpu_busy_mhz` | REAL (nullable) | MEASURED (silicon) | [Intel Turbostat CPU Frequency Reader](../research/system-measurement.md#turbostat-reader) |
| 5 | `cpu_avg_mhz` | REAL (nullable) | MEASURED (silicon) | [Intel Turbostat CPU Frequency Reader](../research/system-measurement.md#turbostat-reader) |
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
| 18 | `ipc` | REAL (nullable) | CALCULATED (silicon) | [Instructions Per Cycle (IPC)](../research/derived-metrics.md#ipc-calculation) |
| 19 | `measurement_source` | TEXT (nullable) |  |  |
| 20 | `l1d_cache_misses` | BIGINT (nullable) |  |  |
| 21 | `l2_cache_misses` | BIGINT (nullable) |  |  |
| 22 | `l3_cache_hits` | BIGINT (nullable) |  |  |
| 23 | `l3_cache_misses` | BIGINT (nullable) |  |  |
| 24 | `extra_metrics_json` | TEXT (nullable) |  |  |
| 25 | `global_run_id` | TEXT (nullable) | SYSTEM |  |
| 26 | `sample_start_ns` | INTEGER (nullable) |  |  |
| 27 | `sample_end_ns` | INTEGER (nullable) |  |  |
| 28 | `interval_ns` | INTEGER (nullable) |  |  |

### `device_telemetry`

**Rows:** 0 — Generic device telemetry samples for non-standard hardware

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

### `ear_decision_log`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `decision_id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER (nullable) | SYSTEM |  |
| 2 | `attempt_id` | INTEGER (nullable) |  |  |
| 3 | `failure_type_id` | TEXT (nullable) |  |  |
| 4 | `attempt_number` | INTEGER (nullable) |  |  |
| 5 | `budget_remaining_uj` | REAL (nullable) |  |  |
| 6 | `action` | TEXT |  |  |
| 7 | `reason` | TEXT (nullable) |  |  |
| 8 | `calibration_cost_uj` | REAL (nullable) |  |  |
| 9 | `calibration_success_prob` | REAL (nullable) |  |  |
| 10 | `decided_at` | TIMESTAMP (nullable) |  |  |

### `ear_policy`

**Rows:** 4

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `ear_policy_id` | INTEGER |  |  |
| 1 | `policy_name` | TEXT |  |  |
| 2 | `description` | TEXT (nullable) |  |  |
| 3 | `calibration_scope` | TEXT |  |  |
| 4 | `created_at` | TIMESTAMP (nullable) |  |  |

### `ear_policy_rules`

**Rows:** 56

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `rule_id` | INTEGER |  |  |
| 1 | `ear_policy_id` | INTEGER |  |  |
| 2 | `failure_type_id` | TEXT |  |  |
| 3 | `max_attempts` | INTEGER |  |  |
| 4 | `cost_threshold_uj` | REAL (nullable) |  |  |
| 5 | `calibrated_success_prob` | REAL (nullable) |  |  |
| 6 | `success_probability_threshold` | REAL (nullable) |  |  |
| 7 | `action` | TEXT |  |  |
| 8 | `priority` | INTEGER |  |  |
| 9 | `created_at` | TIMESTAMP (nullable) |  |  |

### `energy_attribution`

**Rows:** 0 — Phase-attributed energy values per run (planning, tool, synthesis)

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `run_id` | INTEGER | SYSTEM |  |
| 1 | `pkg_energy_uj` | BIGINT (nullable) | MEASURED (silicon) | [RAPL MSR Direct Package Energy](../research/energy-readers.md#rapl-msr-pkg-energy) |
| 2 | `core_energy_uj` | BIGINT (nullable) | MEASURED (silicon) | [RAPL MSR Direct Package Energy](../research/energy-readers.md#rapl-msr-pkg-energy) |
| 3 | `dram_energy_uj` | BIGINT (nullable) | MEASURED (silicon) | [RAPL MSR Direct Package Energy](../research/energy-readers.md#rapl-msr-pkg-energy) |
| 4 | `uncore_energy_uj` | BIGINT (nullable) | MEASURED (silicon) | [RAPL MSR Direct Package Energy](../research/energy-readers.md#rapl-msr-pkg-energy) |
| 5 | `background_energy_uj` | BIGINT (nullable) |  |  |
| 6 | `interrupt_energy_uj` | BIGINT (nullable) |  |  |
| 7 | `scheduler_energy_uj` | BIGINT (nullable) |  |  |
| 8 | `network_wait_energy_uj` | BIGINT (nullable) |  |  |
| 9 | `io_wait_energy_uj` | BIGINT (nullable) |  |  |
| 10 | `disk_energy_uj` | BIGINT (nullable) |  |  |
| 11 | `memory_pressure_energy_uj` | BIGINT (nullable) |  |  |
| 12 | `cache_dram_energy_uj` | BIGINT (nullable) |  |  |
| 13 | `orchestration_energy_uj` | BIGINT (nullable) |  |  |
| 14 | `planning_energy_uj` | BIGINT (nullable) | MEASURED (orchestration) | [Phase Attribution v2 (Direct RAPL Sample Measurement)](../research/derived-metrics.md#phase-attribution-sample-v2) |
| 15 | `execution_energy_uj` | BIGINT (nullable) | MEASURED (orchestration) | [Phase Attribution v2 (Direct RAPL Sample Measurement)](../research/derived-metrics.md#phase-attribution-sample-v2) |
| 16 | `synthesis_energy_uj` | BIGINT (nullable) | MEASURED (orchestration) | [Phase Attribution v2 (Direct RAPL Sample Measurement)](../research/derived-metrics.md#phase-attribution-sample-v2) |
| 17 | `inter_phase_energy_uj` | BIGINT (nullable) | MEASURED (orchestration) | [Phase Attribution v2 (Direct RAPL Sample Measurement)](../research/derived-metrics.md#phase-attribution-sample-v2) |
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
| 37 | `ttft_ms` | REAL (nullable) | MEASURED (application) | [Time to First Token Measurement](../research/derived-metrics.md#ttft-measurement-v1) |
| 38 | `tpot_ms` | REAL (nullable) | MEASURED (application) | [Time Per Output Token Measurement](../research/derived-metrics.md#tpot-measurement-v1) |
| 39 | `created_at` | TIMESTAMP (nullable) |  |  |
| 40 | `updated_at` | TIMESTAMP (nullable) |  |  |
| 41 | `gpu_llm_compute_energy_uj` | BIGINT (nullable) | CALCULATED (application) | [GPU Dynamic Energy Attribution (Exclusive Workload)](../research/gpu-energy.md#gpu-attribution-exclusive-v1) |
| 42 | `gpu_orchestration_energy_uj` | BIGINT (nullable) | CALCULATED (application) | [GPU Dynamic Energy Attribution (Exclusive Workload)](../research/gpu-energy.md#gpu-attribution-exclusive-v1) |
| 43 | `gpu_phase_planning_uj` | BIGINT (nullable) | INFERRED (application) | [GPU Phase Energy Alignment (CPU Proxy)](../research/gpu-energy.md#gpu-phase-alignment-v1) |
| 44 | `gpu_phase_execution_uj` | BIGINT (nullable) | INFERRED (application) | [GPU Phase Energy Alignment (CPU Proxy)](../research/gpu-energy.md#gpu-phase-alignment-v1) |
| 45 | `gpu_phase_synthesis_uj` | BIGINT (nullable) | INFERRED (application) | [GPU Phase Energy Alignment (CPU Proxy)](../research/gpu-energy.md#gpu-phase-alignment-v1) |
| 46 | `gpu_phase_inter_uj` | BIGINT (nullable) | INFERRED (application) | [GPU Phase Energy Alignment (CPU Proxy)](../research/gpu-energy.md#gpu-phase-alignment-v1) |
| 47 | `isolation_level` | TEXT (nullable) |  |  |
| 48 | `idle_policy` | TEXT (nullable) |  |  |

### `energy_derived_metrics`

**Rows:** 0 — Computed energy metrics derived from raw samples via ETL

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

**Rows:** 0 — Domain assignments for energy samples (pkg, core, uncore, dram, gpu)

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
| 3 | `pkg_energy_uj` | INTEGER (nullable) | MEASURED (silicon) | [RAPL MSR Direct Package Energy](../research/energy-readers.md#rapl-msr-pkg-energy) |
| 4 | `core_energy_uj` | INTEGER (nullable) | MEASURED (silicon) | [RAPL MSR Direct Package Energy](../research/energy-readers.md#rapl-msr-pkg-energy) |
| 5 | `uncore_energy_uj` | INTEGER (nullable) | MEASURED (silicon) | [RAPL MSR Direct Package Energy](../research/energy-readers.md#rapl-msr-pkg-energy) |
| 6 | `dram_energy_uj` | INTEGER (nullable) | MEASURED (silicon) | [RAPL MSR Direct Package Energy](../research/energy-readers.md#rapl-msr-pkg-energy) |
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

**Rows:** 0 — Energy samples schema v2 with domain registry integration

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
| 6 | `fidelity` | TEXT |  |  |
| 7 | `error_bound` | TEXT (nullable) |  |  |

### `energy_validation_dcgm_coverage`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `run_id` | INTEGER | SYSTEM |  |
| 1 | `source` | TEXT |  |  |
| 2 | `ready_status` | TEXT |  |  |
| 3 | `ready_wait_ms` | REAL (nullable) |  |  |
| 4 | `sampler_start_utc_ns` | INTEGER (nullable) |  |  |
| 5 | `first_valid_utc_ns` | INTEGER (nullable) |  |  |
| 6 | `last_valid_utc_ns` | INTEGER (nullable) |  |  |
| 7 | `first_valid_mono_ns` | INTEGER (nullable) |  |  |
| 8 | `last_valid_mono_ns` | INTEGER (nullable) |  |  |
| 9 | `row_count` | INTEGER |  |  |
| 10 | `interior_gaps_gt_300ms` | INTEGER (nullable) |  |  |
| 11 | `max_gap_ms` | REAL (nullable) |  |  |
| 12 | `shared_window_coverage` | REAL (nullable) |  |  |
| 13 | `coverage_null_reason` | TEXT (nullable) |  |  |
| 14 | `cap_detected` | INTEGER |  |  |
| 15 | `method_id` | TEXT |  |  |
| 16 | `created_at` | TIMESTAMP (nullable) |  |  |

### `energy_validation_engine_provenance`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `provenance_id` | INTEGER |  |  |
| 1 | `exp_id` | INTEGER | SYSTEM |  |
| 2 | `engine` | TEXT (nullable) |  |  |
| 3 | `engine_version` | TEXT (nullable) |  |  |
| 4 | `server_pid` | INTEGER (nullable) |  |  |
| 5 | `server_start_utc` | TEXT (nullable) |  |  |
| 6 | `port` | INTEGER |  |  |
| 7 | `model_id` | TEXT (nullable) |  |  |
| 8 | `command_json` | TEXT (nullable) |  |  |
| 9 | `command_sha256` | TEXT (nullable) |  |  |
| 10 | `source_path` | TEXT (nullable) |  |  |
| 11 | `source_sha256` | TEXT (nullable) |  |  |
| 12 | `match_method` | TEXT |  |  |
| 13 | `server_instance_id` | TEXT (nullable) |  |  |
| 14 | `worker_pids_json` | TEXT (nullable) |  |  |
| 15 | `listener_identified` | INTEGER |  |  |
| 16 | `worker_identified` | INTEGER |  |  |
| 17 | `worker_association_method` | TEXT (nullable) |  |  |
| 18 | `worker_association_status` | TEXT |  |  |
| 19 | `method_id` | TEXT |  |  |
| 20 | `created_at` | TIMESTAMP (nullable) |  |  |

### `energy_validation_gpu_mem_traffic_runs`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `run_id` | INTEGER | SYSTEM |  |
| 1 | `reader` | TEXT |  |  |
| 2 | `method_id` | TEXT |  |  |
| 3 | `status` | TEXT |  |  |
| 4 | `reason` | TEXT (nullable) |  |  |
| 5 | `pravaha_version` | TEXT (nullable) |  |  |
| 6 | `driver` | TEXT (nullable) |  |  |
| 7 | `cuda` | TEXT (nullable) |  |  |
| 8 | `interval_ms` | INTEGER (nullable) |  |  |
| 9 | `device` | INTEGER (nullable) |  |  |
| 10 | `sample_count` | INTEGER |  |  |
| 11 | `baseline_json` | TEXT (nullable) |  |  |
| 12 | `quality_json` | TEXT (nullable) |  |  |
| 13 | `created_at` | TIMESTAMP (nullable) |  |  |

### `energy_validation_gpu_mem_traffic_samples`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `sample_id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER | SYSTEM |  |
| 2 | `reader` | TEXT |  |  |
| 3 | `t_start_ns` | INTEGER (nullable) |  |  |
| 4 | `t_end_ns` | INTEGER (nullable) |  |  |
| 5 | `interval_ns` | INTEGER (nullable) |  |  |
| 6 | `copy_bytes` | INTEGER (nullable) |  |  |
| 7 | `kernel_read_miss_bytes` | INTEGER (nullable) |  |  |
| 8 | `kernel_write_bytes` | INTEGER (nullable) |  |  |
| 9 | `all_read_miss_bytes` | INTEGER (nullable) |  |  |
| 10 | `all_write_bytes` | INTEGER (nullable) |  |  |
| 11 | `phase` | TEXT (nullable) |  |  |
| 12 | `measurement_scope` | TEXT |  |  |
| 13 | `quality_flag` | TEXT (nullable) | MEASURED (os) | [Normalized Per-Zone Thermal Reader V2](../research/thermal-subsystem.md#thermal-zone-sysfs-v2) |
| 14 | `pravaha_version` | TEXT (nullable) |  |  |

### `energy_validation_seq_attempt_runs`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `attempt_id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER | SYSTEM |  |
| 2 | `child_seq` | INTEGER |  |  |
| 3 | `rep_num` | INTEGER (nullable) |  |  |
| 4 | `workflow_type` | TEXT (nullable) | SYSTEM |  |

### `energy_validation_seq_attempts`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `attempt_id` | INTEGER |  |  |
| 1 | `step_id` | INTEGER |  |  |
| 2 | `attempt_no` | INTEGER |  |  |
| 3 | `execution_status` | TEXT |  |  |
| 4 | `reason_code` | TEXT (nullable) |  |  |
| 5 | `reason_detail` | TEXT (nullable) |  |  |
| 6 | `settle_start_mono_ns` | INTEGER (nullable) |  |  |
| 7 | `settle_end_mono_ns` | INTEGER (nullable) |  |  |
| 8 | `gate_json` | TEXT (nullable) |  |  |
| 9 | `exec_start_mono_ns` | INTEGER (nullable) |  |  |
| 10 | `exec_end_mono_ns` | INTEGER (nullable) |  |  |
| 11 | `exec_start_utc_ns` | INTEGER (nullable) |  |  |
| 12 | `exec_end_utc_ns` | INTEGER (nullable) |  |  |
| 13 | `meas_start_utc_ns` | INTEGER (nullable) |  |  |
| 14 | `meas_end_utc_ns` | INTEGER (nullable) |  |  |
| 15 | `clock_domain` | TEXT |  |  |
| 16 | `baseline_id` | TEXT (nullable) | SYSTEM |  |
| 17 | `exp_id` | INTEGER (nullable) | SYSTEM |  |
| 18 | `created_at` | TIMESTAMP (nullable) |  |  |

### `energy_validation_seq_bracket_eligibility`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `session_id` | INTEGER |  |  |
| 1 | `bracket_id` | TEXT |  |  |
| 2 | `domain` | TEXT |  |  |
| 3 | `eligible` | INTEGER |  |  |
| 4 | `reason` | TEXT (nullable) |  |  |
| 5 | `rule_version` | TEXT |  |  |

### `energy_validation_seq_sessions`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `session_id` | INTEGER |  |  |
| 1 | `name` | TEXT (nullable) |  |  |
| 2 | `plan_path` | TEXT (nullable) |  |  |
| 3 | `plan_source_sha256` | TEXT |  |  |
| 4 | `resolved_plan_json` | TEXT |  |  |
| 5 | `resolved_plan_sha256` | TEXT |  |  |
| 6 | `plan_schema_version` | INTEGER |  |  |
| 7 | `seed` | INTEGER (nullable) |  |  |
| 8 | `realized_order_json` | TEXT (nullable) |  |  |
| 9 | `versions_json` | TEXT (nullable) |  |  |
| 10 | `config_layers_json` | TEXT (nullable) |  |  |
| 11 | `status` | TEXT |  |  |
| 12 | `stop_reason` | TEXT (nullable) |  |  |
| 13 | `start_utc_ns` | INTEGER |  |  |
| 14 | `end_utc_ns` | INTEGER (nullable) |  |  |
| 15 | `created_at` | TIMESTAMP (nullable) |  |  |

### `energy_validation_seq_step_results`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `attempt_id` | INTEGER |  |  |
| 1 | `key` | TEXT |  |  |
| 2 | `value` | TEXT (nullable) |  |  |
| 3 | `unit` | TEXT (nullable) |  |  |

### `energy_validation_seq_steps`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `step_id` | INTEGER |  |  |
| 1 | `session_id` | INTEGER |  |  |
| 2 | `cycle_id` | INTEGER |  |  |
| 3 | `bracket_id` | TEXT |  |  |
| 4 | `condition` | TEXT |  |  |
| 5 | `role` | TEXT |  |  |
| 6 | `kind` | TEXT |  |  |
| 7 | `params_json` | TEXT |  |  |
| 8 | `planned_index` | INTEGER |  |  |

### `energy_validation_server_age`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `run_id` | INTEGER | SYSTEM |  |
| 1 | `exp_id` | INTEGER | SYSTEM |  |
| 2 | `run_start_ns` | INTEGER |  |  |
| 3 | `time_since_boot_s` | REAL (nullable) |  |  |
| 4 | `time_since_driver_load_s` | REAL (nullable) |  |  |
| 5 | `driver_load_source` | TEXT |  |  |
| 6 | `time_since_server_start_s` | REAL (nullable) |  |  |
| 7 | `server_instance_id` | TEXT (nullable) |  |  |
| 8 | `gpu_busy_observed_since_start_s` | REAL (nullable) |  |  |
| 9 | `gpu_temp_c` | REAL (nullable) |  |  |
| 10 | `gpu_sm_clock_mhz` | REAL (nullable) |  |  |
| 11 | `gpu_mem_clock_mhz` | REAL (nullable) |  |  |
| 12 | `method_id` | TEXT |  |  |
| 13 | `created_at` | TIMESTAMP (nullable) |  |  |

### `environment_config`

**Rows:** 1 — Software environment fingerprint — git commit, package versions

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

**Rows:** 0 — Pending and completed ETL job tracking

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

**Rows:** 10 — Evaluation criteria definitions for task quality assessment

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

**Rows:** 0 — One experiment per research question — parent of runs

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
| 21 | `global_exp_id` | TEXT (nullable) |  |  |
| 22 | `experiment_type` | TEXT | MEASURED (orchestration) | [Experiment Classification Metadata](../research/experiment-classification.md#system-metadata-v1) |
| 23 | `experiment_goal` | TEXT (nullable) | MEASURED (orchestration) | [Experiment Classification Metadata](../research/experiment-classification.md#system-metadata-v1) |
| 24 | `experiment_notes` | TEXT (nullable) | MEASURED (orchestration) | [Experiment Classification Metadata](../research/experiment-classification.md#system-metadata-v1) |
| 25 | `hw_id` | INTEGER (nullable) | SYSTEM |  |
| 26 | `env_id` | INTEGER (nullable) |  |  |
| 27 | `is_valid` | INTEGER |  |  |
| 28 | `invalidation_reason` | TEXT (nullable) |  |  |
| 29 | `invalidated_at` | TIMESTAMP (nullable) |  |  |
| 30 | `declared_dedicated_machine` | INTEGER (nullable) | MEASURED (orchestration) | [Experiment Classification Metadata](../research/experiment-classification.md#system-metadata-v1) |
| 31 | `gpu_id` | INTEGER (nullable) |  |  |

### `extension_registry`

**Rows:** 11

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `name` | TEXT |  |  |
| 1 | `version` | TEXT |  |  |
| 2 | `activated_at` | TEXT |  |  |
| 3 | `status` | TEXT |  |  |
| 4 | `migration_version` | TEXT (nullable) |  |  |

### `failure_cost_profile`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `profile_id` | INTEGER |  |  |
| 1 | `failure_type_id` | TEXT |  |  |
| 2 | `experiment_group` | TEXT |  |  |
| 3 | `sample_count` | INTEGER |  |  |
| 4 | `recovery_cost_uj_mean` | REAL (nullable) |  |  |
| 5 | `recovery_cost_uj_median` | REAL (nullable) |  |  |
| 6 | `recovery_cost_uj_p25` | REAL (nullable) |  |  |
| 7 | `recovery_cost_uj_p75` | REAL (nullable) |  |  |
| 8 | `recovery_cost_uj_std` | REAL (nullable) |  |  |
| 9 | `recovery_success_rate` | REAL (nullable) |  |  |
| 10 | `recovery_success_count` | INTEGER (nullable) |  |  |
| 11 | `recovery_attempts_mean` | REAL (nullable) |  |  |
| 12 | `recovery_latency_ms_mean` | REAL (nullable) |  |  |
| 13 | `cost_per_recovery_success` | REAL (nullable) |  |  |
| 14 | `computed_at` | TIMESTAMP (nullable) |  |  |

### `failure_injection_log`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `injection_id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER (nullable) | SYSTEM |  |
| 2 | `attempt_id` | INTEGER (nullable) |  |  |
| 3 | `goal_id` | INTEGER (nullable) |  |  |
| 4 | `scenario_id` | TEXT |  |  |
| 5 | `rule_index` | INTEGER |  |  |
| 6 | `injected_type` | TEXT (nullable) |  |  |
| 7 | `target_step` | INTEGER (nullable) |  |  |
| 8 | `target_phase` | TEXT (nullable) |  |  |
| 9 | `target_tool` | TEXT (nullable) |  |  |
| 10 | `injection_time_ns` | INTEGER (nullable) |  |  |
| 11 | `draw_number` | INTEGER |  |  |
| 12 | `random_seed` | TEXT |  |  |
| 13 | `injection_algorithm_version` | TEXT |  |  |
| 14 | `status` | TEXT |  |  |
| 15 | `skip_reason` | TEXT (nullable) |  |  |
| 16 | `created_at` | TIMESTAMP (nullable) |  |  |

### `failure_taxonomy`

**Rows:** 19

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `failure_type_id` | TEXT |  |  |
| 1 | `domain` | TEXT |  |  |
| 2 | `description` | TEXT (nullable) |  |  |
| 3 | `default_retryable` | INTEGER |  |  |
| 4 | `default_recovery_strategy` | TEXT (nullable) |  |  |
| 5 | `typical_cost_rank` | INTEGER (nullable) |  |  |
| 6 | `created_at` | TIMESTAMP (nullable) |  |  |

### `goal_attempt`

**Rows:** 0 — Individual goal attempt records within goal execution sessions

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `attempt_id` | INTEGER |  |  |
| 1 | `goal_id` | INTEGER |  |  |
| 2 | `run_id` | INTEGER | SYSTEM |  |
| 3 | `attempt_number` | INTEGER |  |  |
| 4 | `is_retry` | INTEGER |  |  |
| 5 | `is_winning` | INTEGER |  |  |
| 6 | `outcome` | TEXT |  |  |
| 7 | `failure_cause` | TEXT (nullable) |  |  |
| 8 | `energy_uj` | INTEGER (nullable) |  |  |
| 9 | `orchestration_uj` | INTEGER (nullable) |  |  |
| 10 | `compute_uj` | INTEGER (nullable) |  |  |
| 11 | `normalized_score` | REAL (nullable) |  |  |
| 12 | `pass_fail` | INTEGER (nullable) |  |  |
| 13 | `status` | TEXT |  |  |
| 14 | `started_at` | TIMESTAMP (nullable) |  |  |
| 15 | `started_at_ns` | INTEGER (nullable) |  |  |
| 16 | `finished_at` | TIMESTAMP (nullable) |  |  |
| 17 | `finished_at_ns` | INTEGER (nullable) |  |  |
| 18 | `updated_at` | TIMESTAMP (nullable) |  |  |
| 19 | `created_at` | TIMESTAMP (nullable) |  |  |
| 20 | `retry_of_attempt_id` | INTEGER (nullable) |  |  |
| 21 | `failure_type` | TEXT (nullable) |  |  |
| 22 | `gpu_energy_uj` | INTEGER (nullable) |  |  |
| 23 | `span_id` | TEXT (nullable) |  |  |

### `goal_execution`

**Rows:** 0 — Goal execution session records — multi-step agentic task tracking

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `goal_id` | INTEGER |  |  |
| 1 | `exp_id` | INTEGER | SYSTEM |  |
| 2 | `first_run_id` | INTEGER (nullable) |  |  |
| 3 | `goal_description` | TEXT |  |  |
| 4 | `goal_type` | TEXT |  |  |
| 5 | `workflow_type` | TEXT | SYSTEM |  |
| 6 | `gpu_total_energy_uj` | INTEGER (nullable) | MEASURED (silicon) | [Intel Iris Xe GPU Energy via MSR 0x641](../research/energy-readers.md#gpu-rapl-pp1-v1) |
| 7 | `gpu_pct_of_pkg` | REAL (nullable) | CALCULATED (silicon) | [GPU Dynamic Energy via Run-Local Adaptive Idle Baseline](../research/energy-readers.md#gpu-dynamic-run-local-v1) |
| 8 | `difficulty_level` | TEXT (nullable) |  |  |
| 9 | `total_attempts` | INTEGER |  |  |
| 10 | `success` | INTEGER |  |  |
| 11 | `winning_run_id` | INTEGER (nullable) |  |  |
| 12 | `total_energy_uj` | INTEGER (nullable) | CALCULATED (silicon) | [Dynamic Energy Calculation](../research/derived-metrics.md#dynamic-energy-calculation) |
| 13 | `successful_energy_uj` | INTEGER (nullable) |  |  |
| 14 | `overhead_energy_uj` | INTEGER (nullable) |  |  |
| 15 | `overhead_fraction` | REAL (nullable) |  |  |
| 16 | `orchestration_fraction` | REAL (nullable) |  |  |
| 17 | `wall_time_ms` | REAL (nullable) |  |  |
| 18 | `task_id` | TEXT (nullable) |  |  |
| 19 | `status` | TEXT |  |  |
| 20 | `started_at` | TIMESTAMP (nullable) |  |  |
| 21 | `finished_at` | TIMESTAMP (nullable) |  |  |
| 22 | `updated_at` | TIMESTAMP (nullable) |  |  |
| 23 | `created_at` | TIMESTAMP (nullable) |  |  |
| 24 | `winning_attempt_id` | INTEGER (nullable) |  |  |
| 25 | `span_id` | TEXT (nullable) |  |  |

### `goal_output`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `goal_id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER | SYSTEM |  |
| 2 | `attempt_id` | INTEGER |  |  |
| 3 | `output_text` | TEXT (nullable) |  |  |
| 4 | `output_type` | TEXT |  |  |
| 5 | `capture_method` | TEXT |  |  |
| 6 | `step_index` | INTEGER (nullable) |  |  |
| 7 | `captured_at` | TEXT |  |  |

### `gpu_config`

**Rows:** 1 — GPU configuration snapshot captured at experiment time

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

**Rows:** 0 — DCGM GPU utilization and memory (NVIDIA Grace only)

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
| 14 | `wasted_energy_uj_real` | INTEGER (nullable) |  |  |
| 15 | `detected_at` | TIMESTAMP |  |  |

### `hardware_config`

**Rows:** 1 — hw_config.json snapshot captured at experiment time

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
| 16 | `gpu_count` | INTEGER (nullable) | MEASURED (silicon) | [GPU Idle Baseline (2-Sigma Method)](../research/gpu-energy.md#gpu-baseline-2sigma-v1) |
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
| 28 | `detected_at` | last_seen           TIMESTAMP (nullable) |  |  |
| 29 | `agent_status` | TEXT (nullable) |  |  |
| 30 | `agent_version` | TEXT (nullable) |  |  |
| 31 | `server_hw_id` | INTEGER (nullable) |  |  |
| 32 | `created_at` | TIMESTAMP (nullable) |  |  |

### `idle_baseline_domains`

**Rows:** 0 — Per-domain idle baseline power values (pkg, core, uncore, dram, gpu)

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `id` | INTEGER |  |  |
| 1 | `baseline_id` | TEXT | SYSTEM |  |
| 2 | `domain_id` | INTEGER |  |  |
| 3 | `power_watts` | REAL |  |  |
| 4 | `std_watts` | REAL |  |  |

### `idle_baselines`

**Rows:** 0 — Idle energy baseline measurements per platform

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
| 15 | `process_count` | INTEGER (nullable) | MEASURED (os) | [OS Scheduler Statistics Reader](../research/system-measurement.md#os-scheduler-reader) |
| 16 | `method` | TEXT (nullable) |  |  |
| 17 | `gpu_power_watts` | REAL (nullable) |  |  |
| 18 | `gpu_std` | REAL (nullable) |  |  |
| 19 | `gpu_method` | TEXT (nullable) |  |  |
| 20 | `std_dev_json` | TEXT (nullable) |  |  |

### `interrupt_samples`

**Rows:** 0 — Context switches and interrupt ticks at 10Hz

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
| 13 | `global_run_id` | TEXT (nullable) | SYSTEM |  |
| 14 | `sample_start_ns` | INTEGER (nullable) |  |  |
| 15 | `sample_end_ns` | INTEGER (nullable) |  |  |
| 16 | `interval_ns` | INTEGER (nullable) |  |  |

### `io_samples`

**Rows:** 0 — Disk read/write byte deltas at 10Hz

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
| 10 | `minor_page_faults` | INTEGER (nullable) | MEASURED (silicon) | [Linux perf Hardware Counters](../research/system-measurement.md#perf-counters) |
| 11 | `major_page_faults` | INTEGER (nullable) | MEASURED (silicon) | [Linux perf Hardware Counters](../research/system-measurement.md#perf-counters) |

### `llm_interactions`

**Rows:** 0 — One row per LLM API call within a run

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
| 8 | `prompt_tokens` | INTEGER (nullable) | MEASURED (application) | [TTFT / TPOT Wall Clock Measurement](../research/derived-metrics.md#ttft-tpot-wall-clock) |
| 9 | `completion_tokens` | INTEGER (nullable) | MEASURED (application) | [TTFT / TPOT Wall Clock Measurement](../research/derived-metrics.md#ttft-tpot-wall-clock) |
| 10 | `total_tokens` | INTEGER (nullable) | MEASURED (application) | [TTFT / TPOT Wall Clock Measurement](../research/derived-metrics.md#ttft-tpot-wall-clock) |
| 11 | `api_latency_ms` | REAL (nullable) | MEASURED (application) | [TTFT / TPOT Wall Clock Measurement](../research/derived-metrics.md#ttft-tpot-wall-clock) |
| 12 | `compute_time_ms` | REAL (nullable) |  |  |
| 13 | `app_throughput_kbps` | REAL (nullable) |  |  |
| 14 | `total_time_ms` | REAL (nullable) |  |  |
| 15 | `preprocess_ms` | REAL (nullable) |  |  |
| 16 | `non_local_ms` | REAL (nullable) |  |  |
| 17 | `local_compute_ms` | REAL (nullable) |  |  |
| 18 | `postprocess_ms` | REAL (nullable) |  |  |
| 19 | `cpu_percent_during_wait` | REAL (nullable) |  |  |
| 20 | `ttft_ms` | REAL (nullable) | MEASURED (application) | [Time to First Token Measurement](../research/derived-metrics.md#ttft-measurement-v1) |
| 21 | `tpot_ms` | REAL (nullable) | MEASURED (application) | [Time Per Output Token Measurement](../research/derived-metrics.md#tpot-measurement-v1) |
| 22 | `token_throughput` | REAL (nullable) |  |  |
| 23 | `streaming_enabled` | INTEGER (nullable) |  |  |
| 24 | `first_token_time_ns` | INTEGER (nullable) |  |  |
| 25 | `last_token_time_ns` | INTEGER (nullable) |  |  |
| 26 | `request_start_ns` | INTEGER (nullable) |  |  |
| 27 | `prefill_energy_uj` | INTEGER (nullable) |  |  |
| 28 | `bytes_sent_approx` | INTEGER (nullable) |  |  |
| 29 | `bytes_recv_approx` | INTEGER (nullable) |  |  |
| 30 | `tcp_retransmits` | INTEGER (nullable) | MEASURED (os) | [Network I/O Measurement](../research/system-measurement.md#network-measurement) |
| 31 | `error_message` | TEXT (nullable) |  |  |
| 32 | `status` | TEXT (nullable) |  |  |
| 33 | `global_run_id` | TEXT (nullable) | SYSTEM |  |
| 34 | `created_at` | TIMESTAMP (nullable) |  |  |
| 35 | `span_id` | TEXT (nullable) |  |  |
| 36 | `ttft_null_reason` | TEXT (nullable) |  |  |

### `machine_setup_history`

**Rows:** 1 — Machine provisioning and configuration change history

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

**Rows:** 102 — Master registry of all measurement methods with formula and provenance

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
| 21 | `doc` | TEXT (nullable) |  |  |
| 22 | `section` | TEXT (nullable) |  |  |
| 23 | `method_anchor` | TEXT (nullable) |  |  |
| 24 | `created_at` | REAL (nullable) |  |  |
| 25 | `updated_at` | REAL (nullable) |  |  |

### `measurement_methodology`

**Rows:** 0 — Per-run methodology audit trail — links runs to method_registry entries

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

**Rows:** 128 — Literature citations per measurement method

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

**Rows:** 230 — Display configuration for all metrics in GUI and reports

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
| 22 | `formula_latex` | TEXT (nullable) |  |  |
| 23 | `source_description` | TEXT (nullable) |  |  |
| 24 | `source_yaml` | TEXT (nullable) |  |  |
| 25 | `goal_id` | TEXT (nullable) |  |  |
| 26 | `active` | INTEGER (nullable) |  |  |
| 27 | `sort_order` | INTEGER (nullable) |  |  |
| 28 | `doc` | TEXT (nullable) |  |  |
| 29 | `section` | TEXT (nullable) |  |  |
| 30 | `method_anchor` | TEXT (nullable) |  |  |
| 31 | `created_at` | REAL (nullable) |  |  |
| 32 | `updated_at` | REAL (nullable) |  |  |

### `migration_history`

**Rows:** 82 — Applied migration versions with checksums

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
| 14 | `source` | TEXT (nullable) |  |  |

### `network_energy_attribution`

**Rows:** 0 — Network wait energy attribution per run and phase

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
| 9 | `nic_activity_validated` | INTEGER (nullable) |  |  |
| 10 | `nic_adjusted_confidence` | REAL (nullable) |  |  |
| 11 | `nic_coverage_fraction` | REAL (nullable) |  |  |
| 12 | `created_at` | TEXT |  |  |

### `nic_samples`

**Rows:** 0 — Network interface byte and packet counters at 10Hz

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

**Rows:** 0 — Grid intensity and environmental conversion factors per country

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
| 22 | `cache_miss_rate` | REAL (nullable) | CALCULATED (silicon) | [LLC Cache Miss Rate](../research/derived-metrics.md#cache-miss-calculation) |
| 23 | `io_wait_ratio` | REAL (nullable) |  |  |
| 24 | `stall_time_ms` | REAL (nullable) |  |  |
| 25 | `sla_violations` | INTEGER (nullable) |  |  |
| 26 | `created_at` | TIMESTAMP (nullable) |  |  |

### `orchestration_events`

**Rows:** 0 — Timeline of agentic orchestration phase transitions

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `event_id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER | SYSTEM |  |
| 2 | `step_index` | INTEGER (nullable) |  |  |
| 3 | `phase` | TEXT (nullable) |  |  |
| 4 | `event_type` | TEXT |  |  |
| 5 | `start_time_ns` | INTEGER | MEASURED (application) | [System Wall Clock](../research/system-measurement.md#system-clock) |
| 6 | `end_time_ns` | INTEGER | MEASURED (application) | [System Wall Clock](../research/system-measurement.md#system-clock) |
| 7 | `duration_ns` | INTEGER | MEASURED (application) | [System Wall Clock](../research/system-measurement.md#system-clock) |
| 8 | `power_watts` | REAL (nullable) |  |  |
| 9 | `cpu_util_percent` | REAL (nullable) |  |  |
| 10 | `interrupt_rate` | REAL (nullable) | MEASURED (os) | [OS Scheduler Statistics Reader](../research/system-measurement.md#os-scheduler-reader) |
| 11 | `event_energy_uj` | INTEGER (nullable) |  |  |
| 12 | `tax_contribution_uj` | INTEGER (nullable) |  |  |
| 13 | `tax_percent` | REAL (nullable) |  |  |
| 14 | `global_run_id` | TEXT (nullable) | SYSTEM |  |
| 15 | `raw_energy_uj` | INTEGER (nullable) |  |  |
| 16 | `cpu_fraction_per_phase` | REAL (nullable) |  |  |
| 17 | `attributed_energy_uj` | INTEGER (nullable) | CALCULATED (os) | [CPU Fraction-Based Energy Attribution](../research/derived-metrics.md#cpu-fraction-attribution) |
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
| 34 | `attempt_id` | INTEGER (nullable) |  |  |
| 35 | `agent_id` | INTEGER (nullable) |  |  |
| 36 | `span_id` | TEXT (nullable) |  |  |

### `orchestration_tax_summary`

**Rows:** 0 — Pre-computed orchestration tax summary per experiment

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
| 4 | `task_category` | TEXT (nullable) |  |  |
| 5 | `metric_type` | TEXT |  |  |
| 6 | `raw_score` | REAL (nullable) |  |  |
| 7 | `normalized_score` | REAL (nullable) |  |  |
| 8 | `pass_fail` | INTEGER (nullable) |  |  |
| 9 | `judge_method` | TEXT |  |  |
| 10 | `judge_count` | INTEGER |  |  |
| 11 | `agreement_score` | REAL (nullable) |  |  |
| 12 | `score_method` | TEXT (nullable) |  |  |
| 13 | `expected_output` | TEXT (nullable) |  |  |
| 14 | `actual_output` | TEXT (nullable) |  |  |
| 15 | `energy_uj_at_judgment` | INTEGER (nullable) |  |  |
| 16 | `manual_reviewed` | INTEGER |  |  |
| 17 | `judged_at` | TIMESTAMP |  |  |
| 18 | `scorer_version` | TEXT (nullable) |  |  |
| 19 | `scorer_config_hash` | TEXT (nullable) |  |  |

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

**Rows:** 10 — GUI page layout configurations

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

**Rows:** 7 — GUI page section definitions

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

### `policy_comparison_results`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `comparison_id` | INTEGER |  |  |
| 1 | `experiment_group_a` | TEXT |  |  |
| 2 | `experiment_group_b` | TEXT |  |  |
| 3 | `policy_a` | TEXT |  |  |
| 4 | `policy_b` | TEXT |  |  |
| 5 | `waste_per_success_a` | REAL (nullable) |  |  |
| 6 | `waste_per_success_b` | REAL (nullable) |  |  |
| 7 | `ratio` | REAL (nullable) |  |  |
| 8 | `success_rate_a` | REAL (nullable) |  |  |
| 9 | `success_rate_b` | REAL (nullable) |  |  |
| 10 | `total_energy_a` | REAL (nullable) |  |  |
| 11 | `total_energy_b` | REAL (nullable) |  |  |
| 12 | `sample_count_a` | INTEGER (nullable) |  |  |
| 13 | `sample_count_b` | INTEGER (nullable) |  |  |
| 14 | `computed_at` | TIMESTAMP (nullable) |  |  |

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

**Rows:** 0 — SPBM per-rail power readings (NVIDIA Grace only)

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

**Rows:** 31 — All SQL queries — no SQL hardcoded in application code

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
| 16 | `source_yaml` | TEXT (nullable) |  |  |
| 17 | `source_tab` | TEXT (nullable) |  |  |
| 18 | `version` | TEXT (nullable) |  |  |
| 19 | `created_at` | TEXT (nullable) |  |  |
| 20 | `updated_at` | TEXT (nullable) |  |  |

### `recovery_events`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `recovery_id` | INTEGER |  |  |
| 1 | `attempt_id` | INTEGER |  |  |
| 2 | `goal_id` | INTEGER |  |  |
| 3 | `agent_id` | INTEGER (nullable) |  |  |
| 4 | `failure_type_id` | TEXT (nullable) |  |  |
| 5 | `recovery_strategy` | TEXT (nullable) |  |  |
| 6 | `rollback_depth_turns` | INTEGER |  |  |
| 7 | `total_trajectory_steps` | INTEGER (nullable) |  |  |
| 8 | `replayed_steps` | INTEGER (nullable) |  |  |
| 9 | `replay_fraction` | REAL (nullable) |  |  |
| 10 | `preserved_state_bytes` | INTEGER (nullable) |  |  |
| 11 | `recovery_point_step` | INTEGER (nullable) |  |  |
| 12 | `recovery_point_phase` | TEXT (nullable) |  |  |
| 13 | `recovery_start_ns` | INTEGER (nullable) |  |  |
| 14 | `recovery_end_ns` | INTEGER (nullable) |  |  |
| 15 | `recovery_energy_uj` | REAL (nullable) |  |  |
| 16 | `recovery_success` | INTEGER (nullable) |  |  |
| 17 | `created_at` | TIMESTAMP (nullable) |  |  |

### `recovery_taxonomy`

**Rows:** 9

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `strategy_id` | TEXT |  |  |
| 1 | `description` | TEXT (nullable) |  |  |
| 2 | `requires_state_preservation` | INTEGER (nullable) |  |  |
| 3 | `typical_rollback_depth` | INTEGER (nullable) |  |  |
| 4 | `created_at` | TIMESTAMP (nullable) |  |  |

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

**Rows:** 0 — Outlier detection results per run with classification and severity

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
| 21 | `outlier_class` | TEXT (nullable) |  |  |
| 22 | `detected_at` | TIMESTAMP |  |  |

### `run_power_limits`

**Rows:** 0 — Power limit state snapshots captured during runs

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `run_id` | INTEGER | SYSTEM |  |
| 1 | `limit_id` | INTEGER |  |  |
| 2 | `value_mw` | REAL |  |  |

### `run_quality`

**Rows:** 0 — Composite run quality scores across multiple quality dimensions

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

**Rows:** 0 — One row per linear or agentic workflow execution (153 columns)

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `run_id` | INTEGER | SYSTEM |  |
| 1 | `exp_id` | INTEGER | SYSTEM |  |
| 2 | `hw_id` | INTEGER (nullable) | SYSTEM | Hardware fingerprint from hw_config.json. Identifies the physical machine. |
| 3 | `baseline_id` | TEXT (nullable) | SYSTEM |  |
| 4 | `run_number` | INTEGER (nullable) | SYSTEM |  |
| 5 | `workflow_type` | TEXT | SYSTEM |  |
| 6 | `start_time_ns` | INTEGER (nullable) | MEASURED (application) | [System Wall Clock](../research/system-measurement.md#system-clock) |
| 7 | `end_time_ns` | INTEGER (nullable) | MEASURED (application) | [System Wall Clock](../research/system-measurement.md#system-clock) |
| 8 | `duration_ns` | INTEGER (nullable) | MEASURED (application) | Inference window only — first token request to last token received. |
| 9 | `total_energy_uj` | INTEGER (nullable) | CALCULATED (silicon) | Hardware counter value in µJ. Zero on modeled platforms. |
| 10 | `dynamic_energy_uj` | INTEGER (nullable) | CALCULATED (silicon) | [Dynamic Energy Calculation](../research/derived-metrics.md#dynamic-energy-calculation) |
| 11 | `baseline_energy_uj` | INTEGER (nullable) | CALCULATED (silicon) | [Dynamic Energy Calculation](../research/derived-metrics.md#dynamic-energy-calculation) |
| 12 | `avg_power_watts` | REAL (nullable) | CALCULATED (silicon) | [Dynamic Energy Calculation](../research/derived-metrics.md#dynamic-energy-calculation) |
| 13 | `pkg_energy_uj` | INTEGER (nullable) | MEASURED (silicon) | [RAPL MSR Direct Package Energy](../research/energy-readers.md#rapl-msr-pkg-energy) |
| 14 | `core_energy_uj` | INTEGER (nullable) | MEASURED (silicon) | [RAPL MSR Direct Package Energy](../research/energy-readers.md#rapl-msr-pkg-energy) |
| 15 | `uncore_energy_uj` | INTEGER (nullable) | MEASURED (silicon) | [RAPL MSR Direct Package Energy](../research/energy-readers.md#rapl-msr-pkg-energy) |
| 16 | `dram_energy_uj` | INTEGER (nullable) | MEASURED (silicon) | [RAPL MSR Direct Package Energy](../research/energy-readers.md#rapl-msr-pkg-energy) |
| 17 | `instructions` | BIGINT (nullable) | MEASURED (silicon) | BIGINT — modern CPUs execute billions/second, exceeds INT32 range. |
| 18 | `cycles` | BIGINT (nullable) | MEASURED (silicon) | [Linux perf Hardware Counters](../research/system-measurement.md#perf-counters) |
| 19 | `ipc` | REAL (nullable) | CALCULATED (silicon) | [Instructions Per Cycle (IPC)](../research/derived-metrics.md#ipc-calculation) |
| 20 | `cache_misses` | BIGINT (nullable) | MEASURED (silicon) | [Linux perf Hardware Counters](../research/system-measurement.md#perf-counters) |
| 21 | `cache_references` | BIGINT (nullable) | MEASURED (silicon) | [Linux perf Hardware Counters](../research/system-measurement.md#perf-counters) |
| 22 | `cache_miss_rate` | REAL (nullable) | CALCULATED (silicon) | [LLC Cache Miss Rate](../research/derived-metrics.md#cache-miss-calculation) |
| 23 | `page_faults` | INTEGER (nullable) | MEASURED (silicon) | [Linux perf Hardware Counters](../research/system-measurement.md#perf-counters) |
| 24 | `major_page_faults` | INTEGER (nullable) | MEASURED (silicon) | [Linux perf Hardware Counters](../research/system-measurement.md#perf-counters) |
| 25 | `minor_page_faults` | INTEGER (nullable) | MEASURED (silicon) | [Linux perf Hardware Counters](../research/system-measurement.md#perf-counters) |
| 26 | `context_switches_voluntary` | INTEGER (nullable) | MEASURED (silicon) | [Linux perf Hardware Counters](../research/system-measurement.md#perf-counters) |
| 27 | `context_switches_involuntary` | INTEGER (nullable) | MEASURED (silicon) | [Linux perf Hardware Counters](../research/system-measurement.md#perf-counters) |
| 28 | `total_context_switches` | INTEGER (nullable) | MEASURED (silicon) | [Linux perf Hardware Counters](../research/system-measurement.md#perf-counters) |
| 29 | `thread_migrations` | INTEGER (nullable) | MEASURED (silicon) | [Linux perf Hardware Counters](../research/system-measurement.md#perf-counters) |
| 30 | `run_queue_length` | REAL (nullable) | MEASURED (os) | [OS Scheduler Statistics Reader](../research/system-measurement.md#os-scheduler-reader) |
| 31 | `kernel_time_ms` | REAL (nullable) | MEASURED (os) | [OS Scheduler Statistics Reader](../research/system-measurement.md#os-scheduler-reader) |
| 32 | `user_time_ms` | REAL (nullable) | MEASURED (os) | [OS Scheduler Statistics Reader](../research/system-measurement.md#os-scheduler-reader) |
| 33 | `frequency_mhz` | REAL (nullable) | MEASURED (silicon) | [Intel Turbostat CPU Frequency Reader](../research/system-measurement.md#turbostat-reader) |
| 34 | `ring_bus_freq_mhz` | REAL (nullable) | MEASURED (silicon) | [Intel Turbostat CPU Frequency Reader](../research/system-measurement.md#turbostat-reader) |
| 35 | `cpu_busy_mhz` | REAL (nullable) | MEASURED (silicon) | [Intel Turbostat CPU Frequency Reader](../research/system-measurement.md#turbostat-reader) |
| 36 | `cpu_avg_mhz` | REAL (nullable) | MEASURED (silicon) | [Intel Turbostat CPU Frequency Reader](../research/system-measurement.md#turbostat-reader) |
| 37 | `package_temp_celsius` | REAL (nullable) | MEASURED (silicon) | [Linux sysfs Thermal Sensor](../research/system-measurement.md#thermal-sensor) |
| 38 | `baseline_temp_celsius` | REAL (nullable) | MEASURED (silicon) | [Linux sysfs Thermal Sensor](../research/system-measurement.md#thermal-sensor) |
| 39 | `start_temp_c` | REAL (nullable) | MEASURED (silicon) | [Linux sysfs Thermal Sensor](../research/system-measurement.md#thermal-sensor) |
| 40 | `max_temp_c` | REAL (nullable) | MEASURED (silicon) | [Linux sysfs Thermal Sensor](../research/system-measurement.md#thermal-sensor) |
| 41 | `min_temp_c` | REAL (nullable) | MEASURED (silicon) | [Linux sysfs Thermal Sensor](../research/system-measurement.md#thermal-sensor) |
| 42 | `thermal_delta_c` | REAL (nullable) | MEASURED (silicon) | [Linux sysfs Thermal Sensor](../research/system-measurement.md#thermal-sensor) |
| 43 | `thermal_during_experiment` | BOOLEAN (nullable) | MEASURED (silicon) | [Linux sysfs Thermal Sensor](../research/system-measurement.md#thermal-sensor) |
| 44 | `thermal_now_active` | BOOLEAN (nullable) | MEASURED (silicon) | [Linux sysfs Thermal Sensor](../research/system-measurement.md#thermal-sensor) |
| 45 | `thermal_since_boot` | BOOLEAN (nullable) | MEASURED (silicon) | [Linux sysfs Thermal Sensor](../research/system-measurement.md#thermal-sensor) |
| 46 | `experiment_valid` | BOOLEAN (nullable) | SYSTEM |  |
| 47 | `c2_time_seconds` | REAL (nullable) | MEASURED (silicon) | [MSR C-State Register Reader](../research/system-measurement.md#msr-reader) |
| 48 | `c3_time_seconds` | REAL (nullable) | MEASURED (silicon) | [MSR C-State Register Reader](../research/system-measurement.md#msr-reader) |
| 49 | `c6_time_seconds` | REAL (nullable) | MEASURED (silicon) | [MSR C-State Register Reader](../research/system-measurement.md#msr-reader) |
| 50 | `c7_time_seconds` | REAL (nullable) | MEASURED (silicon) | [MSR C-State Register Reader](../research/system-measurement.md#msr-reader) |
| 51 | `swap_total_mb` | REAL (nullable) | MEASURED (os) | Linux swap partition/file size — virtual memory overflow from RAM to disk. |
| 52 | `swap_end_free_mb` | REAL (nullable) | MEASURED (os) | [OS Memory Statistics Reader](../research/system-measurement.md#os-memory-reader) |
| 53 | `swap_start_used_mb` | REAL (nullable) | MEASURED (os) | [OS Memory Statistics Reader](../research/system-measurement.md#os-memory-reader) |
| 54 | `swap_end_used_mb` | REAL (nullable) | MEASURED (os) | [OS Memory Statistics Reader](../research/system-measurement.md#os-memory-reader) |
| 55 | `swap_start_cached_mb` | REAL (nullable) | MEASURED (os) | [OS Memory Statistics Reader](../research/system-measurement.md#os-memory-reader) |
| 56 | `swap_end_cached_mb` | REAL (nullable) | MEASURED (os) | [OS Memory Statistics Reader](../research/system-measurement.md#os-memory-reader) |
| 57 | `swap_end_percent` | REAL (nullable) | MEASURED (os) | [OS Memory Statistics Reader](../research/system-measurement.md#os-memory-reader) |
| 58 | `wakeup_latency_us` | REAL (nullable) | MEASURED (os) | [OS Scheduler Statistics Reader](../research/system-measurement.md#os-scheduler-reader) |
| 59 | `interrupt_rate` | REAL (nullable) | MEASURED (os) | [OS Scheduler Statistics Reader](../research/system-measurement.md#os-scheduler-reader) |
| 60 | `thermal_throttle_flag` | INTEGER (nullable) | MEASURED (silicon) | [Linux sysfs Thermal Sensor](../research/system-measurement.md#thermal-sensor) |
| 61 | `rss_memory_mb` | REAL (nullable) | MEASURED (os) | [OS Memory Statistics Reader](../research/system-measurement.md#os-memory-reader) |
| 62 | `vms_memory_mb` | REAL (nullable) | MEASURED (os) | [OS Memory Statistics Reader](../research/system-measurement.md#os-memory-reader) |
| 63 | `total_tokens` | INTEGER (nullable) | MEASURED (application) | [TTFT / TPOT Wall Clock Measurement](../research/derived-metrics.md#ttft-tpot-wall-clock) |
| 64 | `prompt_tokens` | INTEGER (nullable) | MEASURED (application) | [TTFT / TPOT Wall Clock Measurement](../research/derived-metrics.md#ttft-tpot-wall-clock) |
| 65 | `completion_tokens` | INTEGER (nullable) | MEASURED (application) | [TTFT / TPOT Wall Clock Measurement](../research/derived-metrics.md#ttft-tpot-wall-clock) |
| 66 | `dns_latency_ms` | REAL (nullable) |  |  |
| 67 | `api_latency_ms` | REAL (nullable) | MEASURED (application) | [TTFT / TPOT Wall Clock Measurement](../research/derived-metrics.md#ttft-tpot-wall-clock) |
| 68 | `compute_time_ms` | REAL (nullable) |  |  |
| 69 | `bytes_sent` | INTEGER (nullable) | MEASURED (os) | [Network I/O Measurement](../research/system-measurement.md#network-measurement) |
| 70 | `bytes_recv` | INTEGER (nullable) | MEASURED (os) | [Network I/O Measurement](../research/system-measurement.md#network-measurement) |
| 71 | `tcp_retransmits` | INTEGER (nullable) | MEASURED (os) | [Network I/O Measurement](../research/system-measurement.md#network-measurement) |
| 72 | `governor` | TEXT (nullable) | SYSTEM |  |
| 73 | `turbo_enabled` | BOOLEAN (nullable) | SYSTEM |  |
| 74 | `is_cold_start` | BOOLEAN (nullable) | SYSTEM |  |
| 75 | `background_cpu_percent` | REAL (nullable) | MEASURED (os) | [OS Scheduler Statistics Reader](../research/system-measurement.md#os-scheduler-reader) |
| 76 | `process_count` | INTEGER (nullable) | MEASURED (os) | [OS Scheduler Statistics Reader](../research/system-measurement.md#os-scheduler-reader) |
| 77 | `cpu_active_ratio` | REAL (nullable) | MEASURED (os) | [CPU Active Ratio (cross-platform)](../research/energy-readers.md#cpu-active-ratio-v1) |
| 78 | `planning_time_ms` | REAL (nullable) | CALCULATED (orchestration) | [Orchestration Tax Calculation](../research/derived-metrics.md#orchestration-tax-calculation) |
| 79 | `execution_time_ms` | REAL (nullable) | CALCULATED (orchestration) | [Orchestration Tax Calculation](../research/derived-metrics.md#orchestration-tax-calculation) |
| 80 | `synthesis_time_ms` | REAL (nullable) | CALCULATED (orchestration) | [Orchestration Tax Calculation](../research/derived-metrics.md#orchestration-tax-calculation) |
| 81 | `phase_planning_ratio` | REAL (nullable) | CALCULATED (orchestration) | [Orchestration Tax Calculation](../research/derived-metrics.md#orchestration-tax-calculation) |
| 82 | `phase_execution_ratio` | REAL (nullable) | CALCULATED (orchestration) | [Orchestration Tax Calculation](../research/derived-metrics.md#orchestration-tax-calculation) |
| 83 | `phase_synthesis_ratio` | REAL (nullable) | CALCULATED (orchestration) | [Orchestration Tax Calculation](../research/derived-metrics.md#orchestration-tax-calculation) |
| 84 | `llm_calls` | INTEGER (nullable) | SYSTEM |  |
| 85 | `tool_calls` | INTEGER (nullable) | SYSTEM |  |
| 86 | `tools_used` | INTEGER (nullable) | SYSTEM |  |
| 87 | `steps` | INTEGER (nullable) | SYSTEM |  |
| 88 | `avg_step_time_ms` | REAL (nullable) | CALCULATED (orchestration) | [Orchestration Tax Calculation](../research/derived-metrics.md#orchestration-tax-calculation) |
| 89 | `orchestration_cpu_ms` | REAL (nullable) | CALCULATED (orchestration) | [Orchestration Tax Calculation](../research/derived-metrics.md#orchestration-tax-calculation) |
| 90 | `complexity_level` | INTEGER (nullable) | CALCULATED (orchestration) | [Orchestration Complexity Score](../research/derived-metrics.md#complexity-score-calculation) |
| 91 | `complexity_score` | REAL (nullable) | CALCULATED (orchestration) | 0.4·(llm_calls/10) + 0.3·(tool_calls/10) + 0.3·(tokens/1000), capped at 1.0 |
| 92 | `carbon_g` | REAL (nullable) | INFERRED (application) | Calculated from energy × regional grid intensity. Not directly measured. |
| 93 | `water_ml` | REAL (nullable) | INFERRED (application) | Calculated from energy × PUE × WUE factors. Not directly measured. |
| 94 | `methane_mg` | REAL (nullable) | INFERRED (application) | Calculated from carbon_g using IPCC AR6 GWP factors. Not measured. |
| 95 | `energy_per_instruction` | REAL (nullable) | CALCULATED (application) | [Energy Efficiency Metrics](../research/derived-metrics.md#efficiency-metrics-calculation) |
| 96 | `energy_per_cycle` | REAL (nullable) | CALCULATED (application) | [Energy Efficiency Metrics](../research/derived-metrics.md#efficiency-metrics-calculation) |
| 97 | `energy_per_token` | REAL (nullable) | CALCULATED (application) | [Energy Efficiency Metrics](../research/derived-metrics.md#efficiency-metrics-calculation) |
| 98 | `instructions_per_token` | REAL (nullable) | CALCULATED (application) | [Energy Efficiency Metrics](../research/derived-metrics.md#efficiency-metrics-calculation) |
| 99 | `interrupts_per_second` | REAL (nullable) | MEASURED (os) | [OS Scheduler Statistics Reader](../research/system-measurement.md#os-scheduler-reader) |
| 100 | `run_state_hash` | TEXT (nullable) | SYSTEM |  |
| 101 | `pid` | INTEGER (nullable) | SYSTEM |  |
| 102 | `cpu_fraction` | REAL (nullable) | CALCULATED (os) | [CPU Fraction-Based Energy Attribution](../research/derived-metrics.md#cpu-fraction-attribution) |
| 103 | `attributed_energy_uj` | INTEGER (nullable) | CALCULATED (os) | total_energy_uj minus idle baseline × duration. NULL until ETL runs. |
| 104 | `energy_measurement_mode` | TEXT (nullable) | MEASURED | direct (hardware counter) / modeled (no counter) / unavailable |
| 105 | `planning_energy_uj` | INTEGER (nullable) | MEASURED (orchestration) | [Phase Attribution v2 (Direct RAPL Sample Measurement)](../research/derived-metrics.md#phase-attribution-sample-v2) |
| 106 | `execution_energy_uj` | INTEGER (nullable) | MEASURED (orchestration) | [Phase Attribution v2 (Direct RAPL Sample Measurement)](../research/derived-metrics.md#phase-attribution-sample-v2) |
| 107 | `synthesis_energy_uj` | INTEGER (nullable) | MEASURED (orchestration) | [Phase Attribution v2 (Direct RAPL Sample Measurement)](../research/derived-metrics.md#phase-attribution-sample-v2) |
| 108 | `inter_phase_energy_uj` | INTEGER (nullable) | MEASURED (orchestration) | [Phase Attribution v2 (Direct RAPL Sample Measurement)](../research/derived-metrics.md#phase-attribution-sample-v2) |
| 109 | `phase_sample_coverage_pct` | REAL (nullable) | MEASURED (orchestration) | [Phase Attribution v2 (Direct RAPL Sample Measurement)](../research/derived-metrics.md#phase-attribution-sample-v2) |
| 110 | `l1d_cache_misses_total` | BIGINT (nullable) | MEASURED (silicon) | [Perf Cache Counters](../research/derived-metrics.md#perf-cache-counters) |
| 111 | `l2_cache_misses_total` | BIGINT (nullable) | MEASURED (silicon) | [Perf Cache Counters](../research/derived-metrics.md#perf-cache-counters) |
| 112 | `l3_cache_hits_total` | BIGINT (nullable) | MEASURED (silicon) | [Perf Cache Counters](../research/derived-metrics.md#perf-cache-counters) |
| 113 | `l3_cache_misses_total` | BIGINT (nullable) | MEASURED (silicon) | [Perf Cache Counters](../research/derived-metrics.md#perf-cache-counters) |
| 114 | `disk_read_bytes_total` | BIGINT (nullable) | MEASURED (os) | [Disk I/O Statistics](../research/derived-metrics.md#disk-io-stats) |
| 115 | `disk_write_bytes_total` | BIGINT (nullable) | MEASURED (os) | [Disk I/O Statistics](../research/derived-metrics.md#disk-io-stats) |
| 116 | `voltage_vcore_avg` | REAL (nullable) | MEASURED |  |
| 117 | `task_duration_ns` | INTEGER (nullable) | MEASURED (os) | [Task vs Framework Duration Boundary](../research/measurement-boundary.md#measurement-boundary-v1) |
| 118 | `framework_overhead_ns` | INTEGER (nullable) | MEASURED (os) | [Task vs Framework Duration Boundary](../research/measurement-boundary.md#measurement-boundary-v1) |
| 119 | `total_run_duration_ns` | INTEGER (nullable) | MEASURED (os) | Full experiment duration including warmup, cooldown, and overhead. |
| 120 | `duration_includes_overhead` | INTEGER (nullable) | SYSTEM |  |
| 121 | `energy_sample_coverage_pct` | REAL (nullable) | CALCULATED (os) | % of run duration covered by energy samples. <80% = sampling gap. |
| 122 | `avg_task_power_watts` | REAL (nullable) | MEASURED (os) | [Task vs Framework Duration Boundary](../research/measurement-boundary.md#measurement-boundary-v1) |
| 123 | `rapl_before_pretask_uj` | INTEGER (nullable) | MEASURED (os) | [Task vs Framework Duration Boundary](../research/measurement-boundary.md#measurement-boundary-v1) |
| 124 | `rapl_at_t0_uj` | INTEGER (nullable) |  |  |
| 125 | `measurement_log_level` | TEXT (nullable) | SYSTEM |  |
| 126 | `measurement_log_config_hash` | TEXT (nullable) | SYSTEM |  |
| 127 | `observability_overflow` | INTEGER (nullable) | SYSTEM |  |
| 128 | `measurement_heartbeat_s` | REAL (nullable) | SYSTEM |  |
| 129 | `pre_task_energy_uj` | INTEGER (nullable) | MEASURED (os) | [Task vs Framework Duration Boundary](../research/measurement-boundary.md#measurement-boundary-v1) |
| 130 | `pre_task_duration_ns` | INTEGER (nullable) | MEASURED (os) | [Task vs Framework Duration Boundary](../research/measurement-boundary.md#measurement-boundary-v1) |
| 131 | `rapl_after_task_uj` | INTEGER (nullable) | MEASURED (os) | [Task vs Framework Duration Boundary](../research/measurement-boundary.md#measurement-boundary-v1) |
| 132 | `post_task_energy_uj` | INTEGER (nullable) | MEASURED (os) | [Task vs Framework Duration Boundary](../research/measurement-boundary.md#measurement-boundary-v1) |
| 133 | `post_task_duration_ns` | INTEGER (nullable) | MEASURED (os) | [Task vs Framework Duration Boundary](../research/measurement-boundary.md#measurement-boundary-v1) |
| 134 | `framework_overhead_energy_uj` | INTEGER (nullable) | MEASURED (os) | [Task vs Framework Duration Boundary](../research/measurement-boundary.md#measurement-boundary-v1) |
| 135 | `gpu_total_energy_uj` | INTEGER (nullable) | MEASURED (silicon) | [Intel Iris Xe GPU Energy via MSR 0x641](../research/energy-readers.md#gpu-rapl-pp1-v1) |
| 136 | `gpu_baseline_energy_uj` | INTEGER (nullable) | CALCULATED (silicon) | [GPU Dynamic Energy via Baseline Subtraction](../research/energy-readers.md#gpu-dynamic-baseline-v1) |
| 137 | `gpu_dynamic_energy_uj` | INTEGER (nullable) | CALCULATED (silicon) | [GPU Dynamic Energy via Run-Local Adaptive Idle Baseline](../research/energy-readers.md#gpu-dynamic-run-local-v1) |
| 138 | `gpu_pct_of_pkg` | REAL (nullable) | CALCULATED (silicon) | [GPU Dynamic Energy via Run-Local Adaptive Idle Baseline](../research/energy-readers.md#gpu-dynamic-run-local-v1) |
| 139 | `gpu_attribution_method` | TEXT (nullable) | CALCULATED (application) | [GPU Dynamic Energy Attribution (Exclusive Workload)](../research/gpu-energy.md#gpu-attribution-exclusive-v1) |
| 140 | `gpu_count` | INTEGER (nullable) | MEASURED (silicon) | [GPU Idle Baseline (2-Sigma Method)](../research/gpu-energy.md#gpu-baseline-2sigma-v1) |
| 141 | `global_run_id` | TEXT (nullable) | SYSTEM |  |
| 142 | `sync_status` | INTEGER | SYSTEM |  |
| 143 | `sync_samples_status` | INTEGER | SYSTEM |  |
| 144 | `ttft_ms` | REAL (nullable) | MEASURED (application) | [Time to First Token Measurement](../research/derived-metrics.md#ttft-measurement-v1) |
| 145 | `tpot_ms` | REAL (nullable) | MEASURED (application) | [Time Per Output Token Measurement](../research/derived-metrics.md#tpot-measurement-v1) |
| 146 | `gpu_dynamic_method` | TEXT (nullable) |  |  |
| 147 | `gpu_idle_power_w_used` | REAL (nullable) |  |  |
| 148 | `gpu_spbm_total_uj` | INTEGER (nullable) |  |  |
| 149 | `gpu_spbm_dynamic_uj` | INTEGER (nullable) |  |  |
| 150 | `gpu_residual_dynamic_uj` | INTEGER (nullable) |  |  |
| 151 | `spbm_power_sampling_freq_hz` | REAL (nullable) |  |  |
| 152 | `spbm_samples_expected` | INTEGER (nullable) |  |  |
| 153 | `spbm_samples_observed` | INTEGER (nullable) |  |  |
| 154 | `spbm_sample_coverage_pct` | REAL (nullable) |  |  |
| 155 | `spbm_integration_method` | TEXT (nullable) |  |  |
| 156 | `spbm_conversion_loss_uj` | INTEGER (nullable) |  |  |
| 157 | `spbm_conversion_efficiency` | REAL (nullable) |  |  |
| 158 | `span_id` | TEXT (nullable) |  |  |

### `sampler_stats`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `run_id` | INTEGER | SYSTEM |  |
| 1 | `source` | TEXT |  |  |
| 2 | `ticks` | INTEGER (nullable) |  |  |
| 3 | `emitted` | INTEGER (nullable) |  |  |
| 4 | `empty` | INTEGER (nullable) |  |  |
| 5 | `errors` | INTEGER (nullable) |  |  |
| 6 | `max_gap_ns` | INTEGER (nullable) |  |  |
| 7 | `late_join` | INTEGER (nullable) |  |  |
| 8 | `parse_errors` | INTEGER (nullable) |  |  |
| 9 | `requested_hz` | REAL (nullable) |  |  |
| 10 | `probed_hz` | REAL (nullable) |  |  |
| 11 | `schema_max_hz` | REAL (nullable) |  |  |
| 12 | `effective_hz` | REAL (nullable) |  |  |
| 13 | `achieved_hz` | REAL (nullable) |  |  |
| 14 | `rate_limited` | INTEGER (nullable) |  |  |
| 15 | `policy_version` | TEXT (nullable) |  |  |
| 16 | `policy_sha256` | TEXT (nullable) |  |  |

### `sandbox_identity`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `id` | INTEGER |  |  |
| 1 | `sandbox_id` | TEXT |  |  |
| 2 | `name` | TEXT |  |  |
| 3 | `sandbox_path` | TEXT |  |  |
| 4 | `engine_version` | TEXT |  |  |
| 5 | `engine_python` | TEXT |  |  |
| 6 | `adopted_at` | TEXT |  |  |
| 7 | `adopted_from` | TEXT (nullable) |  |  |

### `schema_namespace_tables`

**Rows:** 57

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `namespace` | TEXT |  |  |
| 1 | `object_name` | TEXT |  |  |
| 2 | `object_kind` | TEXT |  |  |
| 3 | `adopted_at` | TIMESTAMP |  |  |

### `schema_version`

**Rows:** 7 — Current schema version tracking

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `version` | INTEGER |  |  |
| 1 | `applied_at` | TIMESTAMP (nullable) |  |  |
| 2 | `description` | TEXT (nullable) |  |  |

### `serving_runtime_snapshots`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `snapshot_id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER (nullable) | SYSTEM |  |
| 2 | `attempt_id` | INTEGER (nullable) |  |  |
| 3 | `timestamp_ns` | INTEGER |  |  |
| 4 | `engine_name` | TEXT |  |  |
| 5 | `engine_type` | TEXT |  |  |
| 6 | `snapshot_type` | TEXT |  |  |
| 7 | `telemetry_scope` | TEXT |  |  |
| 8 | `kv_capacity_tokens` | INTEGER (nullable) |  |  |
| 9 | `kv_occupied_tokens` | INTEGER (nullable) |  |  |
| 10 | `kv_occupancy_fraction` | REAL (nullable) |  |  |
| 11 | `kv_hit_rate_aggregate` | REAL (nullable) |  |  |
| 12 | `kv_num_evictions` | INTEGER (nullable) |  |  |
| 13 | `tier_vram_bytes` | INTEGER (nullable) |  |  |
| 14 | `tier_ram_bytes` | INTEGER (nullable) |  |  |
| 15 | `tier_disk_bytes` | INTEGER (nullable) |  |  |
| 16 | `tier_vram_fraction` | REAL (nullable) |  |  |
| 17 | `tier_ram_fraction` | REAL (nullable) |  |  |
| 18 | `tier_disk_fraction` | REAL (nullable) |  |  |
| 19 | `queue_active` | INTEGER (nullable) |  |  |
| 20 | `queue_waiting` | INTEGER (nullable) |  |  |
| 21 | `queue_completed` | INTEGER (nullable) |  |  |
| 22 | `queue_rejected` | INTEGER (nullable) |  |  |
| 23 | `tokens_per_second` | REAL (nullable) |  |  |
| 24 | `ttft_ms` | REAL (nullable) | MEASURED (application) | [Time to First Token Measurement](../research/derived-metrics.md#ttft-measurement-v1) |
| 25 | `prompt_tokens_total` | INTEGER (nullable) |  |  |
| 26 | `generation_tokens_total` | INTEGER (nullable) |  |  |
| 27 | `extra_json` | TEXT (nullable) |  |  |
| 28 | `created_at` | TIMESTAMP (nullable) |  |  |

### `span_annotations`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `annotation_id` | INTEGER |  |  |
| 1 | `span_id` | TEXT |  |  |
| 2 | `annotation_type` | TEXT |  |  |
| 3 | `annotation_version` | TEXT |  |  |
| 4 | `source` | TEXT |  |  |
| 5 | `created_at` | TIMESTAMP |  |  |
| 6 | `payload` | TEXT |  |  |
| 7 | `provenance_ref` | TEXT (nullable) |  |  |

### `span_attributes`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `attr_id` | INTEGER |  |  |
| 1 | `span_id` | TEXT |  |  |
| 2 | `key` | TEXT |  |  |
| 3 | `value_text` | TEXT (nullable) |  |  |
| 4 | `value_type` | TEXT |  |  |

### `span_events`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `event_id` | INTEGER |  |  |
| 1 | `span_id` | TEXT |  |  |
| 2 | `event_type` | TEXT |  |  |
| 3 | `ts_ns` | INTEGER |  |  |
| 4 | `attributes` | TEXT (nullable) |  |  |

### `span_links`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `link_id` | INTEGER |  |  |
| 1 | `span_id` | TEXT |  |  |
| 2 | `linked_span_id` | TEXT |  |  |
| 3 | `link_type` | TEXT |  |  |

### `span_placements`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `placement_id` | INTEGER |  |  |
| 1 | `span_id` | TEXT |  |  |
| 2 | `node` | TEXT |  |  |
| 3 | `device` | TEXT |  |  |
| 4 | `phase` | TEXT (nullable) |  |  |
| 5 | `start_ns` | INTEGER |  |  |
| 6 | `end_ns` | INTEGER (nullable) |  |  |

### `spans`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `span_id` | TEXT |  |  |
| 1 | `trace_id` | TEXT |  |  |
| 2 | `parent_span_id` | TEXT (nullable) |  |  |
| 3 | `run_id` | INTEGER (nullable) | SYSTEM |  |
| 4 | `vocabulary` | TEXT |  |  |
| 5 | `vocabulary_version` | TEXT |  |  |
| 6 | `kind` | TEXT |  |  |
| 7 | `name` | TEXT |  |  |
| 8 | `start_ns` | INTEGER |  |  |
| 9 | `end_ns` | INTEGER (nullable) |  |  |
| 10 | `start_wall` | TEXT (nullable) |  |  |
| 11 | `status` | TEXT |  |  |
| 12 | `tenant_kind` | TEXT (nullable) |  |  |
| 13 | `tenant_ref` | TEXT (nullable) |  |  |
| 14 | `provenance_ref` | TEXT (nullable) |  |  |

### `sqlite_sequence`

**Rows:** 17 — SQLite internal auto-increment sequence tracking

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `name` | TEXT (nullable) |  |  |
| 1 | `seq` | TEXT (nullable) |  |  |

### `stage_event`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `event_id` | TEXT |  |  |
| 1 | `run_uid` | TEXT |  |  |
| 2 | `run_id` | INTEGER (nullable) | SYSTEM |  |
| 3 | `sandbox_id` | TEXT (nullable) |  |  |
| 4 | `stage_id` | TEXT |  |  |
| 5 | `stage_version` | TEXT |  |  |
| 6 | `status` | TEXT |  |  |
| 7 | `outcome` | TEXT (nullable) |  |  |
| 8 | `reason` | TEXT (nullable) |  |  |
| 9 | `event_seq` | INTEGER (nullable) |  |  |
| 10 | `pid` | INTEGER | SYSTEM |  |
| 11 | `start_ns` | INTEGER (nullable) |  |  |
| 12 | `end_ns` | INTEGER (nullable) |  |  |
| 13 | `counts` | TEXT (nullable) |  |  |
| 14 | `error_ref` | TEXT (nullable) |  |  |
| 15 | `parent_stage_id` | TEXT (nullable) |  |  |
| 16 | `graph_hash` | TEXT |  |  |
| 17 | `blocked_by` | TEXT (nullable) |  |  |
| 18 | `scope` | TEXT |  |  |
| 19 | `created_at` | TEXT |  |  |

### `stage_graph`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `graph_hash` | TEXT |  |  |
| 1 | `graph_id` | TEXT |  |  |
| 2 | `graph_version` | TEXT |  |  |
| 3 | `definition` | TEXT |  |  |
| 4 | `created_at` | TEXT |  |  |

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

### `state_reuse_events`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `reuse_id` | INTEGER |  |  |
| 1 | `recovery_id` | INTEGER (nullable) |  |  |
| 2 | `attempt_id` | INTEGER (nullable) |  |  |
| 3 | `run_id` | INTEGER (nullable) | SYSTEM |  |
| 4 | `reuse_type` | TEXT |  |  |
| 5 | `reuse_source` | TEXT (nullable) |  |  |
| 6 | `tokens_reused` | INTEGER (nullable) |  |  |
| 7 | `tokens_recomputed` | INTEGER (nullable) |  |  |
| 8 | `reuse_fraction` | REAL (nullable) |  |  |
| 9 | `cache_hit` | INTEGER (nullable) |  |  |
| 10 | `cache_query_time_ns` | INTEGER (nullable) |  |  |
| 11 | `state_size_bytes` | INTEGER (nullable) |  |  |
| 12 | `created_at` | TIMESTAMP (nullable) |  |  |

### `state_reuse_taxonomy`

**Rows:** 6

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `reuse_type_id` | TEXT |  |  |
| 1 | `description` | TEXT (nullable) |  |  |
| 2 | `layer` | TEXT |  |  |

### `task_categories`

**Rows:** 0 — Task definitions loaded from config/tasks.yaml

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `task_id` | TEXT |  |  |
| 1 | `category` | TEXT |  |  |

### `task_quality_config`

**Rows:** 17 — Quality thresholds per task type

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `config_id` | INTEGER |  |  |
| 1 | `task_category` | TEXT |  |  |
| 2 | `metric_type` | TEXT |  |  |
| 3 | `judge_method` | TEXT |  |  |
| 4 | `threshold` | REAL |  |  |
| 5 | `dual_judge` | INTEGER |  |  |
| 6 | `created_at` | TIMESTAMP (nullable) |  |  |
| 7 | `n_judges` | INTEGER |  |  |
| 8 | `judge_model_set` | TEXT (nullable) |  |  |
| 9 | `rubric` | TEXT (nullable) |  |  |
| 10 | `success_threshold` | REAL (nullable) |  |  |

### `task_retry_override`

**Rows:** 0 — Per-task retry policy overrides

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `task_category` | TEXT |  |  |
| 1 | `max_retries` | INTEGER |  |  |
| 2 | `policy_name` | TEXT |  |  |

### `te_ambient_samples`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `sample_id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER (nullable) | SYSTEM |  |
| 2 | `session_id` | INTEGER (nullable) |  |  |
| 3 | `sensor_name` | TEXT |  |  |
| 4 | `timestamp_ns` | INTEGER |  |  |
| 5 | `temp_celsius` | REAL (nullable) | MEASURED (os) | [Normalized Per-Zone Thermal Reader V2](../research/thermal-subsystem.md#thermal-zone-sysfs-v2) |
| 6 | `quality_flag` | TEXT | MEASURED (os) | [Normalized Per-Zone Thermal Reader V2](../research/thermal-subsystem.md#thermal-zone-sysfs-v2) |
| 7 | `invalid_reason` | TEXT (nullable) |  |  |

### `te_channel`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `channel_id` | INTEGER |  |  |
| 1 | `machine_profile` | TEXT |  |  |
| 2 | `profile_sha256` | TEXT |  |  |
| 3 | `channel_name` | TEXT |  |  |
| 4 | `source_table` | TEXT |  |  |
| 5 | `source_key` | TEXT |  |  |
| 6 | `unit` | TEXT |  |  |
| 7 | `meaning` | TEXT |  |  |
| 8 | `measurement_kind` | TEXT |  |  |
| 9 | `channel_role` | TEXT |  |  |
| 10 | `parent_channel` | TEXT (nullable) |  |  |
| 11 | `boundary` | TEXT (nullable) |  |  |
| 12 | `attribution_eligible` | INTEGER |  |  |
| 13 | `is_main` | INTEGER |  |  |
| 14 | `loaded_at_utc` | TEXT |  |  |

### `te_clock_anchor`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `anchor_id` | INTEGER |  |  |
| 1 | `session_id` | INTEGER |  |  |
| 2 | `node_name` | TEXT |  |  |
| 3 | `wall_ns` | INTEGER |  |  |
| 4 | `mono_ns` | INTEGER |  |  |
| 5 | `sync_offset_ns` | INTEGER (nullable) |  |  |
| 6 | `sync_method` | TEXT (nullable) |  |  |
| 7 | `offset_to_control_ns` | INTEGER (nullable) |  |  |
| 8 | `stepped` | INTEGER (nullable) |  |  |

### `te_cooling_samples`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `sample_id` | INTEGER |  |  |
| 1 | `session_id` | INTEGER |  |  |
| 2 | `device_id` | INTEGER |  |  |
| 3 | `timestamp_ns` | INTEGER |  |  |
| 4 | `cur_state` | INTEGER (nullable) | MEASURED (os) | [Cooling Device State Reader V1](../research/thermal-subsystem.md#cooling-sysfs-v1) |
| 5 | `quality_flag` | TEXT | MEASURED (os) | [Normalized Per-Zone Thermal Reader V2](../research/thermal-subsystem.md#thermal-zone-sysfs-v2) |
| 6 | `invalid_reason` | TEXT (nullable) |  |  |

### `te_gpu_temp_samples`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `sample_id` | INTEGER |  |  |
| 1 | `session_id` | INTEGER |  |  |
| 2 | `gpu_index` | INTEGER |  |  |
| 3 | `timestamp_ns` | INTEGER |  |  |
| 4 | `temperature_c` | INTEGER (nullable) |  |  |
| 5 | `source` | TEXT |  |  |
| 6 | `quality_flag` | TEXT | MEASURED (os) | [Normalized Per-Zone Thermal Reader V2](../research/thermal-subsystem.md#thermal-zone-sysfs-v2) |
| 7 | `invalid_reason` | TEXT (nullable) |  |  |

### `te_idle_window`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `idle_id` | INTEGER |  |  |
| 1 | `session_id` | INTEGER |  |  |
| 2 | `phase` | TEXT |  |  |
| 3 | `channel_name` | TEXT (nullable) |  |  |
| 4 | `sensor_name` | TEXT (nullable) |  |  |
| 5 | `median_value` | REAL (nullable) |  |  |
| 6 | `mad_value` | REAL (nullable) |  |  |
| 7 | `unit` | TEXT (nullable) |  |  |
| 8 | `sample_count` | INTEGER |  |  |
| 9 | `start_ns` | INTEGER |  |  |
| 10 | `end_ns` | INTEGER |  |  |
| 11 | `method_id` | TEXT |  |  |

### `te_power_rail_samples`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `sample_id` | INTEGER |  |  |
| 1 | `session_id` | INTEGER |  |  |
| 2 | `rail_id` | INTEGER |  |  |
| 3 | `timestamp_ns` | INTEGER |  |  |
| 4 | `interval_ns` | INTEGER (nullable) |  |  |
| 5 | `power_mw` | REAL (nullable) |  |  |
| 6 | `quality_flag` | TEXT | MEASURED (os) | [Normalized Per-Zone Thermal Reader V2](../research/thermal-subsystem.md#thermal-zone-sysfs-v2) |
| 7 | `invalid_reason` | TEXT (nullable) |  |  |

### `te_sensor`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `sensor_id` | INTEGER |  |  |
| 1 | `machine_profile` | TEXT |  |  |
| 2 | `profile_sha256` | TEXT |  |  |
| 3 | `sensor_name` | TEXT |  |  |
| 4 | `source_table` | TEXT |  |  |
| 5 | `zone_type` | TEXT (nullable) |  |  |
| 6 | `zone_index` | INTEGER (nullable) |  |  |
| 7 | `gpu_index` | INTEGER (nullable) |  |  |
| 8 | `canonical_role` | TEXT | MEASURED (os) | [Normalized Per-Zone Thermal Reader V2](../research/thermal-subsystem.md#thermal-zone-sysfs-v2) |
| 9 | `unit` | TEXT |  |  |
| 10 | `nominal_resolution_c` | REAL |  |  |
| 11 | `valid_min_c` | REAL |  |  |
| 12 | `valid_max_c` | REAL |  |  |
| 13 | `expected_update_interval_ms` | REAL (nullable) |  |  |
| 14 | `expected_update_interval_source` | TEXT (nullable) |  |  |
| 15 | `is_main` | INTEGER |  |  |
| 16 | `loaded_at_utc` | TEXT |  |  |

### `te_session`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `session_id` | INTEGER |  |  |
| 1 | `global_session_id` | TEXT |  |  |
| 2 | `node_name` | TEXT |  |  |
| 3 | `role` | TEXT |  |  |
| 4 | `exp_id` | INTEGER (nullable) | SYSTEM |  |
| 5 | `run_id` | INTEGER (nullable) | SYSTEM |  |
| 6 | `profile_id` | TEXT |  |  |
| 7 | `profile_sha256` | TEXT |  |  |
| 8 | `pacing_plugin` | TEXT |  |  |
| 9 | `machine_profile` | TEXT |  |  |
| 10 | `clock_basis` | TEXT |  |  |
| 11 | `status` | TEXT |  |  |
| 12 | `status_reason` | TEXT (nullable) |  |  |
| 13 | `drift_session` | INTEGER (nullable) |  |  |
| 14 | `cooling_complete` | INTEGER (nullable) |  |  |
| 15 | `ambient_status` | TEXT (nullable) |  |  |
| 16 | `start_ns` | INTEGER |  |  |
| 17 | `end_ns` | INTEGER (nullable) |  |  |
| 18 | `created_at` | TIMESTAMP (nullable) |  |  |
| 19 | `pre_window_ns` | INTEGER (nullable) |  |  |
| 20 | `workload_start_ns` | INTEGER (nullable) |  |  |
| 21 | `workload_end_ns` | INTEGER (nullable) |  |  |
| 22 | `cooldown_status` | TEXT (nullable) |  |  |

### `te_session_check`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `check_id` | INTEGER |  |  |
| 1 | `session_id` | INTEGER |  |  |
| 2 | `check_name` | TEXT |  |  |
| 3 | `result` | TEXT |  |  |
| 4 | `value` | REAL (nullable) |  |  |
| 5 | `threshold` | REAL (nullable) |  |  |
| 6 | `note` | TEXT (nullable) |  |  |
| 7 | `method_id` | TEXT (nullable) |  |  |

### `te_thermal_samples`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `sample_id` | INTEGER |  |  |
| 1 | `session_id` | INTEGER |  |  |
| 2 | `zone_id` | INTEGER |  |  |
| 3 | `timestamp_ns` | INTEGER |  |  |
| 4 | `temp_celsius` | REAL (nullable) | MEASURED (os) | [Normalized Per-Zone Thermal Reader V2](../research/thermal-subsystem.md#thermal-zone-sysfs-v2) |
| 5 | `quality_flag` | TEXT | MEASURED (os) | [Normalized Per-Zone Thermal Reader V2](../research/thermal-subsystem.md#thermal-zone-sysfs-v2) |
| 6 | `invalid_reason` | TEXT (nullable) |  |  |

### `thermal_samples`

**Rows:** 0 — Temperature, fan RPM, voltage at 1Hz

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
| 10 | `global_run_id` | TEXT (nullable) | SYSTEM |  |
| 11 | `all_zones_json` | TEXT (nullable) |  |  |
| 12 | `sensor_count` | INTEGER (nullable) |  |  |
| 13 | `sample_start_ns` | INTEGER (nullable) |  |  |
| 14 | `sample_end_ns` | INTEGER (nullable) |  |  |
| 15 | `interval_ns` | INTEGER (nullable) |  |  |

### `thermal_samples_v2`

**Rows:** 0 — Thermal samples schema v2 with zone registry integration

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER | SYSTEM |  |
| 2 | `zone_id` | INTEGER |  |  |
| 3 | `timestamp_ns` | INTEGER |  |  |
| 4 | `temp_celsius` | REAL | MEASURED (os) | [Normalized Per-Zone Thermal Reader V2](../research/thermal-subsystem.md#thermal-zone-sysfs-v2) |
| 5 | `quality_flag` | TEXT | MEASURED (os) | [Normalized Per-Zone Thermal Reader V2](../research/thermal-subsystem.md#thermal-zone-sysfs-v2) |
| 6 | `invalid_reason` | TEXT (nullable) |  |  |
| 7 | `global_run_id` | TEXT (nullable) | SYSTEM |  |

### `thermal_zones`

**Rows:** 0 — Thermal zone inventory and configuration per platform

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `zone_id` | INTEGER |  |  |
| 1 | `machine_id` | TEXT |  |  |
| 2 | `zone_type` | TEXT |  |  |
| 3 | `zone_index` | INTEGER |  |  |
| 4 | `driver` | TEXT (nullable) |  |  |
| 5 | `device` | TEXT (nullable) |  |  |
| 6 | `canonical_role` | TEXT | MEASURED (os) | [Normalized Per-Zone Thermal Reader V2](../research/thermal-subsystem.md#thermal-zone-sysfs-v2) |
| 7 | `source_subsystem` | TEXT |  |  |
| 8 | `first_seen` | TEXT |  |  |
| 9 | `last_seen` | TEXT |  |  |
| 10 | `active` | INTEGER |  |  |

### `tool_failure_events`

**Rows:** 0 — Tool execution failure events with classification and recovery data

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
| 13 | `span_id` | TEXT (nullable) |  |  |

### `tool_selection_events`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `id` | INTEGER |  |  |
| 1 | `run_id` | INTEGER (nullable) | SYSTEM |  |
| 2 | `selector_type` | TEXT |  |  |
| 3 | `available_count` | INTEGER |  |  |
| 4 | `requested_k` | INTEGER |  |  |
| 5 | `actual_k` | INTEGER |  |  |
| 6 | `selected_tool_names` | TEXT (nullable) |  |  |
| 7 | `selector_energy_uj` | INTEGER (nullable) |  |  |
| 8 | `energy_provenance` | TEXT |  |  |
| 9 | `duration_ns` | INTEGER (nullable) | MEASURED (application) | [System Wall Clock](../research/system-measurement.md#system-clock) |
| 10 | `embedding_model` | TEXT (nullable) |  |  |
| 11 | `embedding_dimensions` | INTEGER (nullable) |  |  |
| 12 | `cache_hit` | INTEGER |  |  |
| 13 | `tools_embedded_count` | INTEGER |  |  |
| 14 | `created_at` | TEXT |  |  |

### `writer_idempotency`

**Rows:** 0

| # | Column | Type | Provenance | Note |
|---|---|---|---|---|
| 0 | `idem_key` | TEXT |  |  |
| 1 | `recorded_at` | TEXT |  |  |

---

_Generated 2026-11-24 04:14 from schema version recorded in `migration_history`._
