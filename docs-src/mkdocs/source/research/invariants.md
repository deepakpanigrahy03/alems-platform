---
**Method ID:** invariant_catalog_v1
**Schema version:** 118
**Platforms verified:** NVIDIA Grace GB10 (aarch64), Intel i7-1165G7 (x86_64)
**Status:** PRODUCTION
**Last updated:** 2026-10-03
---

# Invariant Catalog { #invariant-catalog }

## Overview

Invariants are rules stored data must always satisfy, on every machine. The catalog (core/integrity/invariants.yaml) states each rule once; one command checks a store against all of them. It only reads; it never changes data.

```bash
cd <your sandbox>
alems validate invariants                  # whole store
alems validate invariants --since 103      # only runs with run_id above 103
alems validate invariants --json --out report.json
```

Exit code 0 means every rule passed; 4 means at least one rule of severity fail was violated; 3 means the store could not be opened. The store is the current sandbox's store.

## Platform Coverage

| Platform | Architecture | Source | Canonical Role | Confidence | Status |
|---|---|---|---|---|---|
| NVIDIA Grace GB10 | aarch64 | sandbox store | all rules | 1.0 | VERIFIED |
| Intel i7-1165G7 | x86_64 | sandbox store | all rules | 1.0 | VERIFIED |
| AMD Ryzen | x86_64 | sandbox store | all rules | 1.0 | PENDING |
| Apple M1 Pro | arm64 | sandbox store | all rules | 1.0 | PENDING |

Confidence is 1.0 because each rule is a deterministic SQL check on stored values; the only tolerance is declared in the catalog (1000 microjoules for energy rounding, one slowest reader interval for sample timing).

## Schema

| ID | Rule | Severity |
|---|---|---|
| INV-E1 | unavailable values are NULL, never 0 | fail |
| INV-E2 | attributed ≤ dynamic ≤ package energy | fail |
| INV-C1 | dynamic = max(0, package − idle) | fail |
| INV-C2 | phase energies fit inside attributed energy; phases never split an unknown attributed energy | fail |
| INV-E3 | a successful goal has positive energy | fail |
| INV-E4 | goal energy = sum of its attempts (NULL if any attempt is NULL) | fail |
| INV-E5 | attempt energy = attributed energy of its own run | fail |
| INV-B1 | a run's baseline exists in the same store | fail |
| INV-S1a | span tree well formed (one root, parents exist, children inside parent interval) | fail |
| INV-A1 | no two attempts share a run | fail |
| INV-A2 | an attempt's samples lie inside its window | fail |
| INV-M1 | sample coverage of the window at least 80 percent | warn |
| INV-Z1 | no measurement column exactly 0 on 10 consecutive runs | warn |
| INV-D1, INV-D2 | no duplicate rows; no orphan rows | checked by `alems validate persistence` |

Status values in the report: pass, fail, warn, not_evaluable (the store lacks a table or column the rule needs; never counted as pass), delegated.

## Method Provenance

| Item | Value |
|---|---|
| method_id | invariant_catalog_v1 |
| layer | orchestration |
| implementation | core/integrity/catalog.py (evaluator), core/integrity/invariants.yaml (rules, SQL) |
| change rule | a rule's statement changes only through a design amendment; SQL is never adjusted to make data pass |

## Query Reference

**Check new runs after an experiment** (all platforms). Expected: every energy and attempt rule passes.

```bash
alems validate invariants --since <last run before the experiment>
```

**Inspect a failing attempt window (INV-A2)** (all platforms). Expected: sample timestamps between started_at_ns and finished_at_ns.

```sql
SELECT ga.attempt_id, ga.started_at_ns, ga.finished_at_ns,
       (SELECT MIN(timestamp_ns) FROM energy_samples_v2 WHERE run_id = ga.run_id) AS s_min,
       (SELECT MAX(timestamp_ns) FROM energy_samples_v2 WHERE run_id = ga.run_id) AS s_max
FROM goal_attempt ga WHERE ga.attempt_id = <attempt_id>;
```

## Verification

1. `venv/bin/python -m pytest -q tests/test_invariant_catalog.py` from the engine (three tests pass: clean store, planted violations, missing table).
2. In a sandbox, run one experiment and `alems validate invariants --since <n>`.

## Known Limitations

- **INV-S1a currently fails**: span start and end are recorded when spans are written, not when work happened, and paired linear and agentic runs share one trace. The rule is correct; the span writer is scheduled for correction. Workaround: treat S1a counts as the baseline at the gate; only new violation types matter.
- **INV-C1 uses the stored idle energy**: it does not yet recompute idle energy from idle baselines. Workaround: none needed for current stores.
- **Historical rows**: stores older than schema version 118 violate INV-A1 for retried goals. Workaround: `--since`.
