-- migrations/schema/v133_safety_guard.sql
-- DDL only (MSC-4). Immutable after first commit (MSC-1).
-- Hardware safety guard record, written for every experiment whatever
-- extensions are active: one episode per guard activation, the decisions and
-- state changes (events), and the one second watchdog evidence (samples).
-- Missing readings are NULL with a quality word, never 0. Links to extension
-- tables (sequencer, thermal) are plain columns without foreign keys, so core
-- never depends on an extension. Event ids also appear in the host log.
-- Growth: one sample row per second while the guard runs; nothing is deleted
-- by code. Matching CREATE in core/database/schema.py (SC-2).

CREATE TABLE IF NOT EXISTS safety_guard_episodes (
    episode_id       TEXT    PRIMARY KEY,   -- uuid4, one per guard activation (process)
    entry_path       TEXT    NOT NULL,      -- run_experiment | sequencer | thermal_session | other
    pid              INTEGER NOT NULL,
    started_utc_ns   INTEGER NOT NULL,
    started_mono_ns  INTEGER NOT NULL,
    ended_utc_ns     INTEGER,               -- NULL while running or after a crash
    ended_mono_ns    INTEGER,
    seq_session_id   INTEGER,               -- link only (sequencer extension table), no FK
    te_session_id    INTEGER,               -- link only (thermal extension table), no FK
    policy_version   TEXT    NOT NULL,
    config_json      TEXT    NOT NULL,      -- safety.thermal block in force
    config_sha256    TEXT    NOT NULL,
    status           TEXT    NOT NULL CHECK (status IN ('running', 'ended', 'latched', 'crashed')),
    latched          INTEGER NOT NULL DEFAULT 0 CHECK (latched IN (0, 1)),
    latch_reason     TEXT,
    end_reason       TEXT
);

CREATE TABLE IF NOT EXISTS safety_guard_events (
    event_id         TEXT    PRIMARY KEY,   -- uuid4; same id in the host log line
    episode_id       TEXT    NOT NULL REFERENCES safety_guard_episodes(episode_id),
    seq              INTEGER NOT NULL,      -- order within the episode
    event_type       TEXT    NOT NULL CHECK (event_type IN (
                         'episode_start', 'episode_end', 'rest_wait', 'gate_wait', 'gate_open',
                         'gate_timeout', 'idle_headroom_insufficient', 'idle_reference_set',
                         'idle_reference_invalid', 'warn', 'stop', 'latch', 'latch_cleared',
                         'cancel_sent', 'drain_confirmed', 'server_quiescence_unconfirmed',
                         'source_stale', 'no_source')),
    utc_ns           INTEGER NOT NULL,
    mono_ns          INTEGER NOT NULL,
    value_c          REAL,                  -- observed headroom (or other value named in detail_json)
    threshold_c      REAL,
    source           TEXT,                  -- e.g. nvml, thermal_zone
    component        TEXT,                  -- e.g. gpu0, zone name
    limit_type       TEXT,                  -- e.g. slowdown_margin, critical_trip
    quality          TEXT    CHECK (quality IS NULL OR quality IN
                         ('fresh', 'stale', 'unavailable', 'failed', 'invalid')),
    exp_id           INTEGER,               -- links, no FK: an event can precede its run row
    run_id           INTEGER,
    goal_id          INTEGER,
    attempt_no       INTEGER,
    seq_attempt_id   INTEGER,
    te_session_id    INTEGER,
    detail_json      TEXT,
    log_written      INTEGER CHECK (log_written IS NULL OR log_written IN (0, 1)),
    UNIQUE (episode_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_safety_guard_events_run ON safety_guard_events(run_id);
CREATE INDEX IF NOT EXISTS idx_safety_guard_events_type_time ON safety_guard_events(event_type, utc_ns);

CREATE TABLE IF NOT EXISTS safety_guard_samples (
    episode_id            TEXT    NOT NULL REFERENCES safety_guard_episodes(episode_id),
    seq                   INTEGER NOT NULL,  -- watchdog tick number within the episode
    utc_ns                INTEGER NOT NULL,
    mono_ns               INTEGER NOT NULL,
    min_headroom_c        REAL,              -- smallest fresh headroom over required sources; NULL if none
    min_source            TEXT,
    min_component         TEXT,
    min_limit_type        TEXT,
    min_quality           TEXT    NOT NULL CHECK (min_quality IN
                              ('fresh', 'stale', 'unavailable', 'failed', 'invalid')),
    gpu_temp_c            REAL,
    gpu_power_mw          REAL,
    gpu_power_limit_mw    REAL,
    clock_reasons_bitmask INTEGER,           -- raw NVML bitmask
    rise_c_per_s          REAL,              -- warning only
    readings_json         TEXT    NOT NULL,  -- every source: value, quality, observed time, raw counters
    PRIMARY KEY (episode_id, seq)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_safety_guard_samples_time ON safety_guard_samples(utc_ns);
