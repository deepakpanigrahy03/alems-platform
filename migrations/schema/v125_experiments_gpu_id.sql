-- =============================================================================
-- v125_experiments_gpu_id.sql
-- Link each experiment to the gpu_config row (driver, VBIOS) current at creation.
-- =============================================================================
-- environment_config rows are shared across experiments by env_hash, so the
-- link lives on experiments. NULL for historical rows and for machines without
-- a gpu_config row (MIC-1). Rule MSC-4: DDL only. Compare header and footer with
-- v124_experiments_declared_dedicated_machine.sql before running and follow it.
-- =============================================================================

ALTER TABLE experiments ADD COLUMN gpu_id INTEGER REFERENCES gpu_config(gpu_id);
