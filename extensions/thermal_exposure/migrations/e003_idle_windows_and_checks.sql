-- =============================================================================
-- thermal_exposure / e003_idle_windows_and_checks.sql
-- Extension migration: per-recording idle reference and recording checks
-- =============================================================================
--
-- 1. te_session_check.result gains 'unknown': a check that could not be made
--    (sensor, process list or utilisation unavailable) is never stored as
--    'pass'. SQLite cannot change a CHECK in place, so the table is rebuilt
--    and existing rows are copied with an explicit column list (MSC-6).
--
-- 2. te_session gains the evidence that decides whether a recording can be
--    used for a clean comparison. Each item is kept separately; passing one
--    never implies another:
--      idle_reference          'matched'  = idle measured in this recording
--                              'store_baseline' = store idle baseline reused
--      idle_baseline_id        idle_baselines row used when store_baseline
--      idle_equilibrium        1 = pre idle window passed the stability rules
--      declared_idle_state     model server loaded or stopped while idle
--      idle_state_verification evidence that the declared state was true
--      carryover_contaminated  1 = the previous recording did not cool down
--      prev_session_id         that previous recording
--      decision_sensor         sensor used by the cooling and drift rules
--      checks_config_sha256    hash of the idle, cooling and check settings
--
-- 3. te_energy_samples: raw energy counter values read by the recorder only
--    inside idle windows, for machines whose power comes from cumulative
--    counters (no power rails). Mirrors energy_sample_domains. Values that
--    could not be read are NULL with a quality flag, never 0 (MIC-1).
--
-- Names are logical machine profile names, never hostnames.
-- =============================================================================


CREATE TABLE te_session_check_new (
    check_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES te_session(session_id),
    check_name  TEXT    NOT NULL,
    result      TEXT    NOT NULL CHECK(result IN ('pass', 'warn', 'fail', 'unknown')),
    value       REAL,
    threshold   REAL,
    note        TEXT,
    method_id   TEXT,
    UNIQUE(session_id, check_name)
);

INSERT INTO te_session_check_new
    (check_id, session_id, check_name, result, value, threshold, note, method_id)
SELECT check_id, session_id, check_name, result, value, threshold, note, method_id
FROM te_session_check;

DROP TABLE te_session_check;

ALTER TABLE te_session_check_new RENAME TO te_session_check;

CREATE INDEX IF NOT EXISTS idx_te_session_check_session ON te_session_check(session_id);


ALTER TABLE te_session ADD COLUMN idle_reference TEXT
    CHECK(idle_reference IS NULL OR idle_reference IN ('matched', 'store_baseline'));

ALTER TABLE te_session ADD COLUMN idle_baseline_id TEXT;

ALTER TABLE te_session ADD COLUMN idle_equilibrium INTEGER
    CHECK(idle_equilibrium IS NULL OR idle_equilibrium IN (0, 1));

ALTER TABLE te_session ADD COLUMN declared_idle_state TEXT
    CHECK(declared_idle_state IS NULL OR declared_idle_state IN ('server_loaded', 'server_stopped'));

ALTER TABLE te_session ADD COLUMN idle_state_verification TEXT
    CHECK(idle_state_verification IS NULL OR idle_state_verification IN ('pass', 'warn', 'fail', 'unknown'));

ALTER TABLE te_session ADD COLUMN carryover_contaminated INTEGER
    CHECK(carryover_contaminated IS NULL OR carryover_contaminated IN (0, 1));

ALTER TABLE te_session ADD COLUMN prev_session_id INTEGER;

ALTER TABLE te_session ADD COLUMN decision_sensor TEXT;

ALTER TABLE te_session ADD COLUMN checks_config_sha256 TEXT;


CREATE TABLE IF NOT EXISTS te_energy_samples (
    sample_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES te_session(session_id),
    domain_id       INTEGER NOT NULL REFERENCES energy_domains(domain_id),
    timestamp_ns    INTEGER NOT NULL,
    energy_uj       REAL,
    quality_flag    TEXT    NOT NULL CHECK(quality_flag IN ('VALID', 'OUT_OF_RANGE', 'READ_FAILED', 'MISSING')),
    invalid_reason  TEXT,
    CHECK(quality_flag <> 'VALID' OR energy_uj IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_te_energy_samples_session ON te_energy_samples(session_id, timestamp_ns);
