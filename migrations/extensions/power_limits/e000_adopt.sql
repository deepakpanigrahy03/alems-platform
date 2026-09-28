-- migrations/extensions/power_limits/e000_adopt.sql
-- Adoption marker for namespace: power_limits
-- No DDL. Tables already exist from core migrations (v098 to v115).
-- This record declares that future DDL for power_limits tables goes only into
-- migrations/extensions/power_limits/ and never into migrations/schema/.
-- Consistent with the v9000_baseline_adoption.sql pattern.
-- Recorded in migration_history with source='ext:power_limits', version=0.

-- No statements needed. alems_migrate records this file in migration_history
-- when it applies adoption markers.
SELECT 1; -- sentinel so sqlite3 does not error on an empty script
