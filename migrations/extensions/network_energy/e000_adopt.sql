-- migrations/extensions/network_energy/e000_adopt.sql
-- Adoption marker for namespace: network_energy
-- No DDL. Tables already exist from core migrations (v098 to v115).
-- This record declares that future DDL for network_energy tables goes only into
-- migrations/extensions/network_energy/ and never into migrations/schema/.
-- Consistent with the v9000_baseline_adoption.sql pattern.
-- Recorded in migration_history with source='ext:network_energy', version=0.

-- No statements needed. alems_migrate records this file in migration_history
-- when it applies adoption markers.
SELECT 1; -- sentinel so sqlite3 does not error on an empty script
