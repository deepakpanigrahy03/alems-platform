-- s024_thermal_exposure_namespace.sql
-- Records namespace ownership of the thermal_exposure tables.
-- Runs on every machine via alems dev migrate --run.
-- Data only (MSC-4/MSC-6). INSERT OR IGNORE: idempotent.
-- The tables themselves are created by migrations/extensions/thermal_exposure/e001
-- only where the extension is active; these rows record ownership either way.

INSERT OR IGNORE INTO schema_namespace_tables (namespace, object_name, object_kind) VALUES
    ('thermal_exposure', 'te_channel',         'table'),
    ('thermal_exposure', 'te_sensor',          'table'),
    ('thermal_exposure', 'te_session',         'table'),
    ('thermal_exposure', 'te_idle_window',     'table'),
    ('thermal_exposure', 'te_clock_anchor',    'table'),
    ('thermal_exposure', 'te_session_check',   'table'),
    ('thermal_exposure', 'te_ambient_samples', 'table');
