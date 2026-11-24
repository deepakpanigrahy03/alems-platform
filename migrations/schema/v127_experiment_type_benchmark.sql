-- v127: allow experiment_type 'benchmark'
-- Root cause: sequencer benchmark and idle steps (copy rate, idle brackets) are
-- stored as normal runs so every collector and the attempt link apply; they
-- need their own experiment type so LLM analyses can exclude them. SQLite
-- cannot alter a trigger, so both are dropped and recreated with the extended list.
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
            'thermal_session','benchmark'
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
            'thermal_session','benchmark'
        )
        THEN RAISE(ABORT, 'Invalid experiment_type value')
    END;
END;
