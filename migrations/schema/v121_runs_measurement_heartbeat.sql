-- v122: runs.measurement_heartbeat_s (master amendment 5.2a console heartbeat, 39.5.2e)
-- Interval of the in place terminal status line inside the gate.
-- NULL not recorded (gate never engaged or pre v122), 0 off, N seconds.
-- Declared perturbation; C-VAL paper_valid requires 0 or NULL.
ALTER TABLE runs ADD COLUMN measurement_heartbeat_s REAL;
