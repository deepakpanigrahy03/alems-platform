-- v124: experiments.declared_dedicated_machine
-- Root cause: papers state that the measurement machine ran one experiment at a
-- time, but no record of that statement existed per experiment.
-- This column stores the experimenter's declaration from the study config:
-- 1 declared dedicated, 0 declared shared, NULL not declared (MIC-1).
-- It is a declaration, never verified isolation; overlap is measured elsewhere.
-- Applies to: ALL platforms. Existing rows stay NULL.
ALTER TABLE experiments ADD COLUMN declared_dedicated_machine INTEGER CHECK(declared_dedicated_machine IN (0, 1));
