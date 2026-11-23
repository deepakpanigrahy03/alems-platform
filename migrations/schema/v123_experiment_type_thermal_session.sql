-- v123: allow experiment_type 'thermal_session'
-- Root cause: the experiment_type triggers reject any value outside a fixed
-- list; a whole-session thermal recording needs its own experiment type so
-- every query can exclude it from goal runs. SQLite cannot alter a trigger,
-- so both are dropped and recreated with the extended list.
-- Applies to: ALL platforms. No table or row is changed.

DROP TRIGGER IF EXISTS trg_exp_type_insert;

DROP TRIGGER IF EXISTS trg_exp_type_update;

CREATE TRIGGER IF NOT EXISTS trg_exp_type_insert
BEFORE INSERT ON experiments
BEGIN
    SELECT CASE
        WHEN NEW.experiment_type IS NULL OR NEW.experiment_type NOT IN (
            'normal','overhead_study','retry_study','failure_injection',
            'quality_sweep','calibration','ablation','pilot','debug',
            'thermal_session'
        )
        THEN RAISE(ABORT, 'Invalid experiment_type value')
    END;
END;

CREATE TRIGGER IF NOT EXISTS trg_exp_type_update
BEFORE UPDATE OF experiment_type ON experiments
BEGIN
    SELECT CASE
        WHEN NEW.experiment_type IS NULL OR NEW.experiment_type NOT IN (
            'normal','overhead_study','retry_study','failure_injection',
            'quality_sweep','calibration','ablation','pilot','debug',
            'thermal_session'
        )
        THEN RAISE(ABORT, 'Invalid experiment_type value')
    END;
END;
