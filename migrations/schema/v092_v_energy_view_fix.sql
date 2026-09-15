-- =============================================================================
-- v092_v_energy_view_fix.sql
-- Repair stale v_energy view on machines bootstrapped from schema.py
-- =============================================================================
--
-- ROOT CAUSE:
--   schema.py's CREATE_V_ENERGY_VIEW carried the pre-v56 broken definition
--   (flat UNION ALL referencing es.package_energy_uj, which was never a
--   real column — the real column is pkg_energy_uj). Migration v56 fixed
--   this correctly years ago, but schema.py itself was never updated to
--   match. Every machine that replayed the full migration chain from v1
--   (GN100, AMD, Lenovo, UBUNTU2505) got v56's correct pivoted view.
--   Every machine bootstrapped fresh from schema.py at a snapshot version
--   (debian-vm, fedora-vm, both starting from v86) inherited the stale,
--   broken view silently — SQLite does not validate a view's column
--   references until something queries it, so this went undetected until
--   v090's table RENAME triggered SQLite's view revalidation.
--
--   schema.py's CREATE_V_ENERGY_VIEW is corrected in this same commit
--   (see core/database/schema.py) so this bug cannot recur on any future
--   fresh install. This migration repairs machines that already have the
--   stale view baked in.
--
-- SAFE ON ALL MACHINES:
--   Machines that already have the correct v56 view (GN100, AMD, Lenovo,
--   UBUNTU2505) — DROP + CREATE with the identical correct definition is
--   a no-op in effect. Machines with the stale view get it replaced.
--   No data is touched — this is a view definition only, not a table.
-- =============================================================================

DROP VIEW IF EXISTS v_energy;

CREATE VIEW v_energy AS

-- NEW normalized rows (GN100, Apple, AMD, TAMU, all future platforms)
-- Pivot: one row per sample_id, domains spread into named columns.
-- GPU domain appears multiple times: one column per source.
SELECT
    esv2.sample_id,
    esv2.run_id,
    esv2.timestamp_ns,
    esv2.interval_ns,
    src.name                                                        AS source_name,
    MAX(CASE WHEN esd.domain_id = 1  THEN esd.energy_uj END)       AS package_energy_uj,
    MAX(CASE WHEN esd.domain_id = 2  THEN esd.energy_uj END)       AS core_energy_uj,
    MAX(CASE WHEN esd.domain_id = 3  THEN esd.energy_uj END)       AS uncore_energy_uj,
    MAX(CASE WHEN esd.domain_id = 4  THEN esd.energy_uj END)       AS dram_energy_uj,
    MAX(CASE WHEN esd.domain_id = 5  THEN esd.energy_uj END)       AS cpu_p_energy_uj,
    MAX(CASE WHEN esd.domain_id = 6  THEN esd.energy_uj END)       AS cpu_e_energy_uj,
    MAX(CASE WHEN esd.domain_id = 7 AND esd.source_id = 2
             THEN esd.energy_uj END)                                AS gpu_spbm_energy_uj,
    MAX(CASE WHEN esd.domain_id = 7 AND esd.source_id = 4
             THEN esd.energy_uj END)                                AS gpu_dcgm_energy_uj,
    MAX(CASE WHEN esd.domain_id = 7 AND esd.source_id = 3
             THEN esd.energy_uj END)                                AS gpu_nvml_energy_uj,
    MAX(CASE WHEN esd.domain_id = 7 AND esd.source_id = 7
             THEN esd.energy_uj END)                                AS gpu_smi_energy_uj,
    MAX(CASE WHEN esd.domain_id = 7 AND esd.source_id = 8
             THEN esd.energy_uj END)                                AS gpu_pp1_energy_uj,
    MAX(CASE WHEN esd.domain_id = 11 THEN esd.energy_uj END)       AS unified_energy_uj,
    MAX(CASE WHEN esd.domain_id = 12 THEN esd.energy_uj END)       AS cpu_apple_energy_uj,
    MAX(CASE WHEN esd.domain_id = 13 THEN esd.energy_uj END)       AS gpu_apple_energy_uj,
    MAX(CASE WHEN esd.domain_id = 15 THEN esd.energy_uj END)       AS nvlink_c2c_energy_uj,
    MAX(CASE WHEN esd.domain_id = 8  THEN esd.energy_uj END)       AS ccd0_energy_uj,
    MAX(CASE WHEN esd.domain_id = 9  THEN esd.energy_uj END)       AS ccd1_energy_uj,
    MAX(CASE WHEN esd.domain_id = 10 THEN esd.energy_uj END)       AS iodie_energy_uj,
    MAX(CASE WHEN esd.domain_id = 20 THEN esd.energy_uj END)       AS dla_energy_uj
FROM energy_samples_v2 esv2
JOIN energy_sources        src ON src.source_id = esv2.source_id
JOIN energy_sample_domains esd ON esd.sample_id = esv2.sample_id
GROUP BY
    esv2.sample_id,
    esv2.run_id,
    esv2.timestamp_ns,
    esv2.interval_ns,
    src.name

UNION ALL

-- LEGACY RAPL rows (untouched forever)
-- pkg_energy_uj mapped to package_energy_uj for uniform column name.
-- Non-RAPL columns are NULL — SUM/AVG skip them automatically.
SELECT
    es.sample_id,
    es.run_id,
    es.timestamp_ns,
    es.interval_ns,
    'RAPL'                  AS source_name,
    es.pkg_energy_uj        AS package_energy_uj,
    es.core_energy_uj,
    es.uncore_energy_uj,
    es.dram_energy_uj,
    NULL                    AS cpu_p_energy_uj,
    NULL                    AS cpu_e_energy_uj,
    NULL                    AS gpu_spbm_energy_uj,
    NULL                    AS gpu_dcgm_energy_uj,
    NULL                    AS gpu_nvml_energy_uj,
    NULL                    AS gpu_smi_energy_uj,
    es.gpu_energy_uj        AS gpu_pp1_energy_uj,
    NULL                    AS unified_energy_uj,
    NULL                    AS cpu_apple_energy_uj,
    NULL                    AS gpu_apple_energy_uj,
    NULL                    AS nvlink_c2c_energy_uj,
    NULL                    AS ccd0_energy_uj,
    NULL                    AS ccd1_energy_uj,
    NULL                    AS iodie_energy_uj,
    NULL                    AS dla_energy_uj
FROM energy_samples es;

PRAGMA integrity_check;

INSERT INTO schema_version (version, applied_at, description)
VALUES (92, datetime('now'),
    'Repair v_energy view: replace stale pre-v56 definition with correct pivoted view (schema.py/migrations divergence fix)');
