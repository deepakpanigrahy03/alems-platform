-- v119: measurement window observability provenance on runs (39.5.2 WP 2b).
-- measurement_log_level: effective logging level inside the gate (INV-P1).
-- measurement_log_config_hash: hash of the effective per component logging config (C-LOG rule 4).
-- observability_overflow: contract plumbing for C-VAL; NULL not recorded (pre 2b or gate
-- never engaged), 0 gate engaged without overflow, 1 a non lossy buffer overflowed.
ALTER TABLE runs ADD COLUMN measurement_log_level TEXT;
ALTER TABLE runs ADD COLUMN measurement_log_config_hash TEXT;
ALTER TABLE runs ADD COLUMN observability_overflow INTEGER;

INSERT INTO schema_version (version, applied_at, description)
VALUES (119, datetime('now'), 'runs: measurement_log_level, measurement_log_config_hash, observability_overflow (39.5.2b)');
