-- migrations/schema/v122_sampler_stats.sql
-- DDL only (MSC-4). Immutable after first commit (MSC-1).
-- Per run, per source record of sampling behaviour: ticks, loss, gaps, and the
-- requested, probed, bound, effective and achieved rate under the declared
-- sampling policy (config/sampling.yaml, identified by version and SHA256).
-- Tested by invariant S2. Matching CREATE in core/database/schema.py (SC-2).

CREATE TABLE IF NOT EXISTS sampler_stats (
    run_id          INTEGER NOT NULL,
    source          TEXT    NOT NULL,   -- schema source kind (RAPL, SPBM, IOKIT, ...); 'none' if the run recorded no stats
    ticks           INTEGER,            -- sampling ticks attempted
    emitted         INTEGER,            -- samples written
    empty           INTEGER,            -- ticks where the reader returned nothing (energy carried to next sample)
    errors          INTEGER,            -- ticks that raised (energy carried to next sample for counters)
    max_gap_ns      INTEGER,            -- longest interval between emitted samples
    late_join       INTEGER,            -- 1 if the sampling thread did not stop within 5 s
    parse_errors    INTEGER,            -- reader side parse failures in this run (IOKit blocks without elapsed time)
    requested_hz    REAL,               -- policy rate (config/sampling.yaml)
    probed_hz       REAL,               -- measured capability of this machine's source (NULL if unknown)
    schema_max_hz   REAL,               -- SDK safe maximum for the source kind
    effective_hz    REAL,               -- min(requested, probed, schema bound)
    achieved_hz     REAL,               -- (emitted - 1) / sampled span
    rate_limited    INTEGER,            -- 1 if effective_hz < requested_hz
    policy_version  TEXT,
    policy_sha256   TEXT,               -- SHA256 of config/sampling.yaml in force
    PRIMARY KEY (run_id, source)
);

CREATE INDEX IF NOT EXISTS idx_sampler_stats_run_id ON sampler_stats(run_id);
