-- Migration v097: Add rapl_at_t0_uj to runs
-- Enables exact pre_task energy on SPBM platforms without residual formula.
-- rapl_at_t0_uj = read_energy() immediately after start_measurement() at t0.
ALTER TABLE runs ADD COLUMN rapl_at_t0_uj INTEGER;
