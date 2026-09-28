-- s018_namespace_adoption.sql
-- Seeds schema_namespace_tables with every non-core table and view
-- from config/schema/table_ownership.yaml (surveyed 2026-09-28).
-- Core-owned objects are excluded; this table records only namespace ownership.
-- INSERT OR IGNORE is safe for reruns (idempotent).

-- attribution
INSERT OR IGNORE INTO schema_namespace_tables (namespace, object_name, object_kind) VALUES
    ('attribution', 'energy_attribution', 'table'),
    ('attribution', 'v_attribution_summary', 'view'),
    ('attribution', 'v_fraction_verification', 'view');

-- failure_recovery
INSERT OR IGNORE INTO schema_namespace_tables (namespace, object_name, object_kind) VALUES
    ('failure_recovery', 'failure_injection_log', 'table'),
    ('failure_recovery', 'goal_attempt', 'table'),
    ('failure_recovery', 'recovery_events', 'table'),
    ('failure_recovery', 'recovery_taxonomy', 'table'),
    ('failure_recovery', 'tool_failure_events', 'table'),
    ('failure_recovery', 'v_failure_cost_by_experiment', 'view'),
    ('failure_recovery', 'v_failure_cost_comparison', 'view'),
    ('failure_recovery', 'v_failure_energy_taxonomy', 'view'),
    ('failure_recovery', 'v_recovery_cost_by_depth', 'view');

-- gui
INSERT OR IGNORE INTO schema_namespace_tables (namespace, object_name, object_kind) VALUES
    ('gui', 'analysis_domain_config', 'table'),
    ('gui', 'analysis_view_config', 'table'),
    ('gui', 'metric_analysis_domains', 'table'),
    ('gui', 'metric_display_registry', 'table'),
    ('gui', 'page_configs', 'table'),
    ('gui', 'page_metric_configs', 'table'),
    ('gui', 'page_sections', 'table'),
    ('gui', 'page_templates', 'table');

-- llm_tracking
INSERT OR IGNORE INTO schema_namespace_tables (namespace, object_name, object_kind) VALUES
    ('llm_tracking', 'machine_setup_history', 'table');

-- network_energy
INSERT OR IGNORE INTO schema_namespace_tables (namespace, object_name, object_kind) VALUES
    ('network_energy', 'network_energy_attribution', 'table');

-- orchestration
INSERT OR IGNORE INTO schema_namespace_tables (namespace, object_name, object_kind) VALUES
    ('orchestration', 'ear_decision_log', 'table'),
    ('orchestration', 'ear_policy', 'table'),
    ('orchestration', 'ear_policy_rules', 'table'),
    ('orchestration', 'failure_taxonomy', 'table'),
    ('orchestration', 'orchestration_events', 'table'),
    ('orchestration', 'orchestration_tax_summary', 'table'),
    ('orchestration', 'retry_policy', 'table'),
    ('orchestration', 'serving_runtime_snapshots', 'table'),
    ('orchestration', 'task_retry_override', 'table'),
    ('orchestration', 'tool_selection_events', 'table'),
    ('orchestration', 'orchestration_analysis', 'view');

-- outlier_detection
INSERT OR IGNORE INTO schema_namespace_tables (namespace, object_name, object_kind) VALUES
    ('outlier_detection', 'outlier_detection_config', 'table'),
    ('outlier_detection', 'run_outliers', 'table');

-- output_quality
INSERT OR IGNORE INTO schema_namespace_tables (namespace, object_name, object_kind) VALUES
    ('output_quality', 'hallucination_events', 'table'),
    ('output_quality', 'output_quality', 'table'),
    ('output_quality', 'output_quality_judges', 'table'),
    ('output_quality', 'run_quality', 'table'),
    ('output_quality', 'v_quality_energy_frontier', 'view');

-- power_limits
INSERT OR IGNORE INTO schema_namespace_tables (namespace, object_name, object_kind) VALUES
    ('power_limits', 'power_limits', 'table'),
    ('power_limits', 'power_rail_samples', 'table'),
    ('power_limits', 'run_power_limits', 'table');

-- task_categories
INSERT OR IGNORE INTO schema_namespace_tables (namespace, object_name, object_kind) VALUES
    ('task_categories', 'task_categories', 'table');

-- telemetry
INSERT OR IGNORE INTO schema_namespace_tables (namespace, object_name, object_kind) VALUES
    ('telemetry', 'cache_state_snapshots', 'table'),
    ('telemetry', 'device_telemetry', 'table'),
    ('telemetry', 'state_reuse_events', 'table'),
    ('telemetry', 'state_reuse_taxonomy', 'table'),
    ('telemetry', 'v_state_reuse_impact', 'view');
