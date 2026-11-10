-- s023_energy_validation_namespace.sql
-- Records namespace ownership of the energy_validation tables.
-- Runs on every machine via alems dev migrate --run.
-- Data only (MSC-4/MSC-6). INSERT OR IGNORE: idempotent.
-- The table itself is created by migrations/extensions/energy_validation/e001
-- only where the extension is active; this row records ownership either way.

INSERT OR IGNORE INTO schema_namespace_tables (namespace, object_name, object_kind) VALUES
    ('energy_validation', 'energy_validation_dcgm_coverage', 'table');
