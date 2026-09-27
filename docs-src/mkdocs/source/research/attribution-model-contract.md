---
**Method IDs:** legacy_v1, conservation_residual
**Schema versions:** v115 (attribution_model_metadata), v116 (attribution_residual)
**Platforms verified:** NVIDIA Grace GB10 (aarch64), Intel i7-1165G7 (x86_64)
**Status:** PRODUCTION
**Last updated:** 2026-09-27
---

# A-LEMS Attribution Model Contract and Conservation Report

## Overview

A-LEMS attributes measured hardware energy to the spans that consumed it.
Attribution answers: of the 5803mJ this package consumed, how much went to
the LLM computation, how much to orchestration, how much to the tool calls,
and how much is unaccounted for?

The attribution model contract (DESIGN_CHUNK39_v4 section 10) defines how
attribution models are registered, versioned, and invoked.
The conservation report verifies that attribution is internally consistent:
attributed energy plus residual must equal measured energy within tolerance.

## Attribution Model: legacy_v1

The first attribution model plugin wraps the existing ETL chain unchanged.
It is registered under the entry point alems.attribution.models as legacy_v1
version 1.0.0 and records the following metadata on every energy_attribution row:

| Column | Value | Meaning |
|---|---|---|
| attribution_model_version | v2 | ETL algorithm version (not plugin version) |
| isolation_level | exclusive | run had exclusive hardware access |
| idle_policy | legacy_baseline_subtraction | idle energy subtraction method |

### Isolation levels

| Level | Meaning |
|---|---|
| exclusive | Only this run was active. Attribution is direct. |
| partitioned | Hardware partitioned between known tenants (MIG, vCPU). |
| shared | Multiple workloads shared hardware. Attribution is estimated. |

Paper-grade reports refuse shared isolation without a calibration reference.

### Idle policy

legacy_baseline_subtraction: measured idle baseline energy is subtracted from
raw package energy before attribution. The baseline is the median idle power
from idle_baselines table, multiplied by run duration. This is an allocation
convention, not a physical claim. The idle energy subtracted is recorded in
energy_attribution.background_energy_uj.

Alternative policies (pluggable, post Gate F): idle_time_share,
equal_share, reservation_share.

## Conservation Report: attribution_residual

After every run, A-LEMS computes a conservation check per energy domain.
The result is stored in attribution_residual.

### Schema

| Column | Type | Meaning |
|---|---|---|
| run_id | INTEGER | Links to runs table |
| domain | TEXT | Energy domain: pkg, core, dram, uncore, total |
| window_label | TEXT | task_window in foundation phases |
| measured_uj | BIGINT | Raw hardware reading from runs table |
| attributed_uj | BIGINT | Sum of attribution columns for this domain |
| residual_uj | BIGINT | measured minus attributed (may be negative) |
| tolerance_uj | BIGINT | max(10000, 1% of measured) |
| status | TEXT | ok / over_attributed / under_attributed / not_applicable |

### Domain map

| Domain | measured_uj source | attributed_uj source |
|---|---|---|
| pkg | runs.pkg_energy_uj | energy_attribution.pkg_energy_uj |
| core | runs.core_energy_uj | energy_attribution.core_energy_uj |
| dram | runs.dram_energy_uj | energy_attribution.dram_energy_uj |
| uncore | runs.uncore_energy_uj | energy_attribution.uncore_energy_uj |
| total | runs.total_energy_uj | sum of all phase columns in energy_attribution |

Phase columns summed for total domain:
planning_energy_uj, execution_energy_uj, synthesis_energy_uj,
inter_phase_energy_uj, orchestration_energy_uj, llm_compute_energy_uj,
tool_energy_uj, retry_energy_uj, failed_tool_energy_uj,
rejected_generation_energy_uj, llm_wait_energy_uj, unattributed_energy_uj.

### Status values

| Status | Meaning | Action |
|---|---|---|
| ok | residual within tolerance | None |
| over_attributed | More energy attributed than measured | Investigate: possible Bug-8 overlap or shared isolation |
| under_attributed | Measured energy not fully attributed | Investigate: possible missing phase or reader gap |
| not_applicable | measured_uj is zero | Hardware not available on this platform |

### Tolerance

Tolerance is max(10000 uj, 1% of measured).
10000 uj minimum prevents false positives on very short or low-power runs.
1% fraction matches the golden-run tolerance policy (DESIGN section 12).

A residual of zero (status=ok) on exclusive isolation means the ETL chain
accounts for every microjoule the hardware reported.

## Platform Coverage

| Platform | pkg | core | dram | uncore | total | Status |
|---|---|---|---|---|---|---|
| NVIDIA Grace GB10 | VERIFIED | VERIFIED | not_applicable | VERIFIED | VERIFIED | PRODUCTION |
| Intel i7-1165G7 | VERIFIED | VERIFIED | not_applicable | VERIFIED | VERIFIED | PRODUCTION |
| Apple M1 Pro | PLANNED | PLANNED | not_applicable | not_applicable | PLANNED | PLANNED |
| AMD Ryzen | PENDING | PENDING | not_applicable | PENDING | PENDING | PENDING |

dram shows not_applicable on all current platforms because DRAM energy
is not measured via RAPL on these machines (DRAM RAPL domain requires
specific hardware support).

## Query Reference

### 1. Conservation status for all runs in an experiment

```sql
SELECT r.run_id, r.workflow_type,
       ar.domain, ar.measured_uj, ar.attributed_uj,
       ar.residual_uj, ar.tolerance_uj, ar.status
FROM attribution_residual ar
JOIN runs r ON r.run_id = ar.run_id
WHERE r.exp_id = <exp_id>
ORDER BY r.run_id, ar.domain;
```

### 2. Runs with over-attribution (possible Bug-8 overlap)

```sql
SELECT r.run_id, r.workflow_type,
       ar.domain, ar.measured_uj, ar.attributed_uj,
       ar.residual_uj
FROM attribution_residual ar
JOIN runs r ON r.run_id = ar.run_id
WHERE ar.status = 'over_attributed'
ORDER BY ar.residual_uj ASC;
```

### 3. Runs with under-attribution (measurement gaps)

```sql
SELECT r.run_id, r.workflow_type,
       ar.domain, ar.measured_uj, ar.attributed_uj,
       ar.residual_uj
FROM attribution_residual ar
JOIN runs r ON r.run_id = ar.run_id
WHERE ar.status = 'under_attributed'
ORDER BY ar.residual_uj DESC;
```

### 4. Attribution model metadata per run

```sql
SELECT r.run_id, r.workflow_type,
       ea.attribution_model_version,
       ea.isolation_level,
       ea.idle_policy,
       ea.attribution_coverage_pct
FROM energy_attribution ea
JOIN runs r ON r.run_id = ea.run_id
WHERE r.exp_id = <exp_id>
ORDER BY r.run_id;
```

### 5. Conservation summary across an experiment

```sql
SELECT ar.domain,
       COUNT(*) AS run_count,
       SUM(CASE WHEN ar.status = 'ok' THEN 1 ELSE 0 END) AS ok_count,
       SUM(CASE WHEN ar.status = 'over_attributed' THEN 1 ELSE 0 END) AS over_count,
       SUM(CASE WHEN ar.status = 'under_attributed' THEN 1 ELSE 0 END) AS under_count,
       AVG(ar.residual_uj) AS avg_residual_uj
FROM attribution_residual ar
JOIN runs r ON r.run_id = ar.run_id
WHERE r.exp_id = <exp_id>
GROUP BY ar.domain
ORDER BY ar.domain;
```

### 6. Verify attribution_residual populated for all runs

```sql
SELECT r.run_id,
       COUNT(ar.residual_id) AS residual_rows
FROM runs r
LEFT JOIN attribution_residual ar ON ar.run_id = r.run_id
WHERE r.exp_id = <exp_id>
GROUP BY r.run_id
HAVING residual_rows = 0;
-- Returns empty set when all runs have residual rows.
```

## Verification

After any experiment run, verify residual is populated:

```bash
DB=$(python3 -c "import sys; sys.path.insert(0,'scripts/tools'); \
from path_loader import get_alems_db_path; print(get_alems_db_path())")

sqlite3 "$DB" "
SELECT run_id, domain, measured_uj, residual_uj, status
FROM attribution_residual
ORDER BY residual_id DESC LIMIT 10;
"
```

Expected: 5 rows per run (pkg, core, dram, uncore, total).
dram and uncore may show not_applicable on platforms without those domains.
status=ok means conservation holds within tolerance.

## Known Limitations

- **Foundation phases report only**: conservation_residual never fails
  a run or alters results. It is an integrity check only.
  Enforcement (failing runs with large residuals) is a post Gate F decision.

- **task_window only**: all residual rows use window_label=task_window.
  Pre-task and post-task window residuals are not computed in foundation.
  This means Bug-8 post-task energy appears as over-attribution on the
  total domain in some runs.

- **Raw connection**: conservation_residual uses db.db.conn directly
  (same transitional pattern as legacy_v1 ETL). Post Gate F this will
  accept a storage handle. Backlog B39-4c-1.

- **dram not_applicable on all current platforms**: DRAM RAPL requires
  specific hardware support not present on GN100 or Lenovo.
  Accurate DRAM attribution requires hardware upgrade or estimation.

- **Shared isolation not calibrated**: attribution_model_version=v2 and
  idle_policy=legacy_baseline_subtraction are only valid under exclusive
  isolation. Shared isolation results have higher residuals and should not
  be used for paper-grade comparisons without calibration.
