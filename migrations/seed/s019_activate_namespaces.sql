-- s019_activate_namespaces.sql
-- Updates the 11 legacy extension_registry rows to active.
-- Runs on every machine via alems dev migrate --run.
-- Safe to rerun: WHERE status = 'legacy' is a no-op if already active.

UPDATE extension_registry SET status = 'active' WHERE name IN (
    'attribution',
    'failure_recovery',
    'gui',
    'llm_tracking',
    'network_energy',
    'orchestration',
    'outlier_detection',
    'output_quality',
    'power_limits',
    'task_categories',
    'telemetry'
) AND status = 'legacy';
