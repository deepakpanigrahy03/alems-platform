---
**Method ID:** failure_taxonomy_v1
**Schema version:** 98 (failure_taxonomy, recovery_taxonomy), 99 (reconstructed tables, winning_attempt_id)
**Platforms verified:** NVIDIA Grace GB10 (aarch64), Intel i7-1165G7 (x86_64), AMD Ryzen (x86_64), Apple M1 Pro (arm64)
**Status:** PRODUCTION
**Last updated:** 2026-09-19
---

# Failure and Recovery Taxonomy { #failure-taxonomy-v1 }

## Overview

This document describes the extensible failure and recovery taxonomy introduced in schema versions 98 and 99.
Before this taxonomy, failure types were hardcoded as CHECK constraints in `tool_failure_events` and `goal_attempt`.
Adding a new failure class required a schema migration and a code change.
After this taxonomy, adding a new failure type is one INSERT into `failure_taxonomy` with zero code changes.

The taxonomy serves two papers directly.
Paper 8 (Failure-Class-Aware Retry Policies) uses `failure_taxonomy` to define the eight failure classes it profiles and to calibrate the Energy-Aware Retry (EAR) policy.
Stephen's MLSys paper uses `recovery_taxonomy` to classify rollback strategies and measure recovery depth.

The taxonomy also fixes the `goal_attempt.outcome` semantics.
Previously, `outcome` conflated what happened (success vs failure) with why it failed (hallucination, timeout, api_error).
After schema version 99, `outcome` holds only `success`, `failure`, or `partial`.
The reason for failure moves to `failure_type`, which is a foreign key into `failure_taxonomy`.

The `winning_attempt_id` column added to `goal_execution` provides a direct FK path from a goal to its winning attempt.
Previously, `winning_run_id` pointed to `runs`, requiring a join through `goal_attempt` to reach the attempt record.
Both columns coexist; `winning_run_id` is deprecated in documentation but retained for backward compatibility.

## Platform Coverage

The taxonomy layer is platform-agnostic per design principle P7.
Failure types and recovery strategies are independent of energy source (RAPL, SPBM, DCGM, IOKit).
The taxonomy tables are created on every platform by the same migration.

| Platform | Architecture | Taxonomy Created | Failure Recording | Status |
|---|---|---|---|---|
| NVIDIA Grace GB10 | aarch64 | YES (v098) | tool_failure_events populated | VERIFIED |
| Intel i7-1165G7 | x86_64 | YES (v098) | tool_failure_events populated | VERIFIED |
| AMD Ryzen | x86_64 | YES (v098) | tool_failure_events populated | VERIFIED |
| Apple M1 Pro | arm64 | YES (v098) | tool_failure_events populated | PENDING |

## Schema

### failure_taxonomy

| Column | Type | Nullable | Description |
|---|---|---|---|
| failure_type_id | TEXT PK | NO | Stable identifier. Referenced by FKs in tool_failure_events and goal_attempt. Never changes once seeded. |
| domain | TEXT | NO | Coarse family: reasoning, execution, communication, resource, validation, platform_specific. |
| description | TEXT | YES | Human-readable description for paper tables and documentation. |
| default_retryable | INTEGER | NO | Advisory default: 1 = retrying this failure type is usually productive. |
| default_recovery_strategy | TEXT | YES | Advisory default strategy_id. EAR policy rules can override. |
| typical_cost_rank | INTEGER | YES | Seed-time advisory cost ordering. A3 profiling computes real values. |
| created_at | TIMESTAMP | YES | Row creation time. |

### recovery_taxonomy

| Column | Type | Nullable | Description |
|---|---|---|---|
| strategy_id | TEXT PK | NO | Stable identifier. Referenced by FK in tool_failure_events. |
| description | TEXT | YES | Human-readable description. |
| requires_state_preservation | INTEGER | NO | 1 = orchestration state from failed attempt must be held in memory during recovery. |
| typical_rollback_depth | INTEGER | YES | 0 = no rollback. N = retry from N turns back. -1 = full goal restart. |
| created_at | TIMESTAMP | YES | Row creation time. |

### goal_attempt changes (schema version 99)

| Column | Change | Before | After |
|---|---|---|---|
| outcome | CHECK simplified | 6 values: success/failure/hallucination/timeout/context_overflow/api_error | 3 values: success/failure/partial |
| failure_cause | REMOVED | CHECK(api_error/tool_error/wrong_answer/timeout/context_overflow/rate_limit) | Column dropped. Content migrated to failure_type. |
| failure_type | CHECK replaced with FK | Free TEXT, no constraint | FK REFERENCES failure_taxonomy(failure_type_id) |

### goal_execution changes (schema version 99)

| Column | Change | Notes |
|---|---|---|
| winning_attempt_id | ADDED | FK to goal_attempt(attempt_id). Backfilled from is_winning=1. winning_run_id retained for backward compat. |

### tool_failure_events changes (schema version 99)

| Column | Change | Before | After |
|---|---|---|---|
| failure_type | CHECK replaced with FK | CHECK(7 values + other) | FK REFERENCES failure_taxonomy(failure_type_id) |
| recovery_strategy | CHECK replaced with FK | CHECK(5 values) | FK REFERENCES recovery_taxonomy(strategy_id) |
| wasted_energy_uj | CHECK added | No constraint | CHECK(IS NULL OR >= 0) |

## Method Provenance

The taxonomy tables are lookup infrastructure, not measurement methods.
They do not appear in `COLUMN_PROVENANCE` or `METHOD_CONFIDENCE` in `core/utils/provenance.py` because they do not produce measurement values for the `runs` table.
No MPC-2 or MPC-3 entries are required.

The columns derived from taxonomy data that do appear in `runs`-adjacent tables use these existing method registrations:

| Column | Table | method_id | Provenance type |
|---|---|---|---|
| failure_type | tool_failure_events | (runtime classification, no method_id) | SYSTEM |
| failure_type | goal_attempt | (runtime classification, no method_id) | SYSTEM |
| wasted_energy_uj | tool_failure_events | energy_attribution_v1 | CALCULATED |

## Query Reference

All queries below are tested against the NVIDIA Grace GB10 platform.
Replace `'<your-hostname>'` with `socket.gethostname()` output on your machine (PDS-8).

**List all canonical failure types with their domain and default strategy**
Applies to: all platforms.
Expected output: 13 rows, one per canonical failure type.

```sql
SELECT failure_type_id,
       domain,
       default_retryable,
       default_recovery_strategy,
       typical_cost_rank
FROM failure_taxonomy
WHERE domain != 'platform_specific'
ORDER BY typical_cost_rank;
```

**Failure distribution by domain across all experiments**
Applies to: all platforms.
Expected output: one row per domain showing count and fraction.

```sql
SELECT ft.domain,
       COUNT(tfe.failure_id)                         AS event_count,
       ROUND(COUNT(tfe.failure_id) * 100.0 /
             SUM(COUNT(tfe.failure_id)) OVER (), 1)  AS pct
FROM tool_failure_events tfe
JOIN failure_taxonomy ft ON tfe.failure_type = ft.failure_type_id
GROUP BY ft.domain
ORDER BY event_count DESC;
```

**Goal attempt outcome breakdown with failure type**
Applies to: all platforms.
Expected output: one row per (outcome, failure_type) combination.

```sql
SELECT ga.outcome,
       ga.failure_type,
       ft.domain,
       COUNT(*)          AS attempt_count,
       AVG(ga.energy_uj) AS mean_energy_uj
FROM goal_attempt ga
LEFT JOIN failure_taxonomy ft ON ga.failure_type = ft.failure_type_id
GROUP BY ga.outcome, ga.failure_type
ORDER BY ga.outcome, attempt_count DESC;
```

**Verify winning_attempt_id is populated for all successful goals**
Applies to: all platforms.
Expected output: 0 rows (no violations).

```sql
SELECT ge.goal_id, ge.winning_run_id, ge.winning_attempt_id
FROM goal_execution ge
WHERE ge.success = 1
  AND ge.winning_attempt_id IS NULL;
```

**Cross-domain energy waste: mean wasted energy per failure domain**
Applies to: all platforms where wasted_energy_uj is populated by ETL.
Expected output: one row per domain with mean wasted energy.

```sql
SELECT ft.domain,
       COUNT(tfe.failure_id)          AS event_count,
       AVG(tfe.wasted_energy_uj)      AS mean_wasted_uj,
       SUM(tfe.wasted_energy_uj)      AS total_wasted_uj
FROM tool_failure_events tfe
JOIN failure_taxonomy ft ON tfe.failure_type = ft.failure_type_id
WHERE tfe.wasted_energy_uj IS NOT NULL
GROUP BY ft.domain
ORDER BY total_wasted_uj DESC;
```

## Verification

Step-by-step commands to confirm correct operation after migration.
Every expected output is shown exactly as it appears on GN100 (aarch64).
A developer on any other machine should see identical check_ids and violation
counts — row counts (V2, V3) will differ based on local experiment history.

**Step 1: Set DB path.**

```bash
DB=$(python3 -c "import sys; sys.path.insert(0,'scripts/tools'); from path_loader import get_alems_db_path; print(get_alems_db_path())")
echo $DB
```

Expected: a path ending in `experiments.db`. If empty, check `scripts/tools/path_loader.py`.

**Step 2: Confirm all migrations and seeds are applied.**

```bash
sqlite3 "$DB" "SELECT version, type, filename, status FROM migration_history WHERE filename LIKE '%failure_taxonomy%' OR filename LIKE '%s014%' OR filename LIKE '%s015%' ORDER BY type, version;"
```

Expected output (exact filenames, status=applied for all):

98|schema|v098_failure_taxonomy_tables.sql|applied
99|schema|v099_failure_taxonomy_wiring.sql|applied
|seed|s014_failure_taxonomy_seed.sql|applied
|seed|s015_add_crashed_failure_type.sql|applied


**Step 3: Run all validation checks.**

```bash
sqlite3 "$DB" < scripts/validation/validate_a1_taxonomy.sql
```

Expected output (exact — copy this as your baseline):

V1|orphaned failure_type in tool_failure_events|0
V2|goal_attempt row count|2022
V3|tool_failure_events row count|287
V4|successful goals missing winning_attempt_id|0
V5a|failure_taxonomy row count|14
V5b|recovery_taxonomy row count|9
V6|orphaned failure_type in goal_attempt|0
V7|legacy outcome values in goal_attempt|0
V8|failure_cause column present in goal_attempt (expected)|1
V9|winning_attempt_id column exists in goal_execution|1


All `violations` = 0. All `present` = 1. V2 and V3 row counts are GN100
baselines — your machine will show higher counts if more experiments have run.
V5a = 14: 13 canonical taxonomy entries (s014) plus `crashed` (s015).
V8 = 1 confirms `failure_cause` column is intentionally retained (design
decision 8.6-A1: keep coarse classification alongside fine-grained FK).

**Step 4: Confirm taxonomy contents.**

```bash
sqlite3 "$DB" "SELECT failure_type_id, domain, default_retryable, typical_cost_rank FROM failure_taxonomy ORDER BY typical_cost_rank;"
```

Expected: 14 rows. `crashed` appears last (cost_rank=14, default_retryable=0).
All 13 canonical entries from s014 appear with cost_rank 1 through 13.

**Step 5: Confirm recorder is taxonomy-driven.**

```bash
python3 -c "
import sqlite3, sys
sys.path.insert(0, '.')
from core.database.tool_failure_recorder import _get_valid_types, _FALLBACK_FAILURE_TYPES
db = sqlite3.connect('$DB')
types = _get_valid_types(db)
print('loaded from taxonomy:', len(types), 'types')
print('includes crashed:', 'crashed' in types)
print('includes hallucination:', 'hallucination' in types)
print('includes semantic_error:', 'semantic_error' in types)
db.close()
"
```

Expected:

loaded from taxonomy: 14 types
includes crashed: True
includes hallucination: True
includes semantic_error: True


**Step 6: Run provenance regression.**

```bash
bash scripts/test_provenance.sh
```

Expected: 31 passed, 3 warnings, 0 failures.
The 3 warnings (`rapl_msr_pkg_energy`, `ml_energy_estimator`, `dummy_energy_reader`)
are pre-existing orphaned entries — not introduced by 8.6-A1.

**Step 7: Run methodology ref validator.**

```bash
python3 scripts/tools/validate_methodology_refs.py
```

Expected: 31 passed, 3 warnings, 0 failures.
`failure_taxonomy_v1` does NOT appear as a warning — it was intentionally
removed from `methodology_docs.yaml` because taxonomy is lookup infrastructure,
not a measurement method. The doc `failure-taxonomy.md` stands independently.

## Taxonomy Contents

The 14 canonical failure types as seeded on GN100 (your authoritative reference):

| failure_type_id | domain | retryable | cost_rank | recovery |
|---|---|---|---|---|
| hallucination | reasoning | YES | 1 | full_restart |
| semantic_error | reasoning | YES | 2 | turn_retry |
| capability_error | reasoning | NO | 3 | abort |
| auth_error | communication | NO | 4 | abort |
| timeout | execution | YES | 5 | backoff_retry |
| malformed_input | execution | YES | 6 | turn_retry |
| tool_error | execution | YES | 7 | immediate_retry |
| malformed_output | validation | YES | 8 | immediate_retry |
| json_parse | validation | YES | 9 | immediate_retry |
| api_error | communication | YES | 10 | backoff_retry |
| network_error | communication | YES | 11 | backoff_retry |
| rate_limit | communication | YES | 12 | backoff_retry |
| not_found | communication | NO | 13 | skip |
| crashed | execution | NO | 14 | abort |

`crashed` (s015): process or harness crash during goal execution.
Terminal state is unknown — not retryable. Added from GN100 production data
where 69 `goal_attempt` rows had `failure_type='crashed'` with no taxonomy entry.

**Step 3: Confirm failure_cause column is gone.**

```bash
sqlite3 "$DB" "PRAGMA table_info(goal_attempt);" | grep failure_cause
```

Expected: no output.

**Step 4: Confirm taxonomy FKs are active.**

```bash
sqlite3 "$DB" "PRAGMA foreign_key_list(tool_failure_events);" | grep -E "failure_taxonomy|recovery_taxonomy"
```

Expected: two rows referencing failure_taxonomy and recovery_taxonomy.

**Step 5: Run provenance regression.**

```bash
bash scripts/test_provenance.sh
```

Expected: 22/22 pass.

## Known Limitations

- **'other' failure_type migration:** Any existing `tool_failure_events` rows with `failure_type = 'other'` are remapped to `tool_error` during the v099 reconstruction. `other` was a catch-all with no semantic precision. If precise classification of those 287 historical rows is needed for paper analysis, a manual re-classification pass should be run after migration.
  Workaround: Query `WHERE failure_type = 'tool_error' AND created_at < '<v099_applied_at>'` to identify potentially remapped rows.

- **wasted_energy_uj historical rows:** `wasted_energy_uj` is ETL-populated and is NULL for most historical rows. Cross-domain energy waste queries will undercount until the ETL backfill runs.
  Workaround: Run `python scripts/etl/energy_attribution_etl.py --backfill-all` before using wasted_energy_uj in analysis.

- **winning_attempt_id for partial goals:** Goals with `success = 0` and no `is_winning = 1` attempt have `winning_attempt_id = NULL` by design. Queries joining on `winning_attempt_id` must use LEFT JOIN for these goals.
  Workaround: None needed — NULL is correct for failed goals.

- **platform_specific taxonomy extension:** The `domain = 'platform_specific'` value allows platform teams to INSERT additional failure types. These rows are not in the canonical seed. Cross-platform comparisons must account for the possibility that a `failure_type_id` present on one machine does not exist on another.
  Workaround: Filter `WHERE ft.domain != 'platform_specific'` for cross-platform aggregate queries.
