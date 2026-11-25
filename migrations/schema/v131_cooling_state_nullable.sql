-- v131: cooling_samples.cur_state allows NULL
-- Root cause: cur_state was NOT NULL, so a cooling device whose state file was
-- missing or unreadable was stored as 0 with quality MISSING or READ_FAILED,
-- and 0 is also a real state (cooling off). From now on a missing state is
-- NULL. Existing rows are copied unchanged (stored sample values are never
-- changed by a migration); v_cooling_samples_valid gives cur_state only for
-- VALID rows, so old 0-with-MISSING rows read as missing there.
-- Applies to: ALL platforms. Columns, indexes and values unchanged otherwise.

CREATE TABLE cooling_samples_new (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL,
    device_id       INTEGER NOT NULL,
    timestamp_ns    INTEGER NOT NULL,
    cur_state       INTEGER,
    quality_flag    TEXT    NOT NULL DEFAULT 'VALID',
    invalid_reason  TEXT,
    global_run_id   TEXT,
    FOREIGN KEY(run_id)    REFERENCES runs(run_id),
    FOREIGN KEY(device_id) REFERENCES cooling_devices(device_id)
);

INSERT INTO cooling_samples_new
    (id, run_id, device_id, timestamp_ns, cur_state, quality_flag, invalid_reason, global_run_id)
SELECT id, run_id, device_id, timestamp_ns, cur_state, quality_flag, invalid_reason, global_run_id
FROM cooling_samples;

DROP TABLE cooling_samples;

ALTER TABLE cooling_samples_new RENAME TO cooling_samples;

CREATE INDEX IF NOT EXISTS idx_cooling_samples_run_time
    ON cooling_samples(run_id, timestamp_ns);

CREATE INDEX IF NOT EXISTS idx_cooling_samples_device_time
    ON cooling_samples(device_id, timestamp_ns);

CREATE INDEX IF NOT EXISTS idx_cooling_samples_throttle
    ON cooling_samples(run_id, quality_flag, cur_state);

CREATE VIEW IF NOT EXISTS v_cooling_samples_valid AS
SELECT id, run_id, device_id, timestamp_ns,
       CASE WHEN quality_flag = 'VALID' THEN cur_state END AS cur_state,
       cur_state AS cur_state_raw,
       quality_flag, invalid_reason, global_run_id
FROM cooling_samples;
