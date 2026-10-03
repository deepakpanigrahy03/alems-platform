---
**Method ID:** goal_attempt_run_accounting_v1
**Schema version:** 118 (one run per attempt, view v_runs_goal)
**Platforms verified:** NVIDIA Grace GB10 (aarch64), Intel i7-1165G7 (x86_64)
**Status:** PRODUCTION
**Last updated:** 2026-10-03
---

# Energy Accounting: Goals, Attempts, Runs { #energy-accounting }

## Overview

A goal is one task A-LEMS was asked to solve. When a try fails and the retry policy allows it, the goal is tried again. Every try is an attempt. Every attempt that opened a measurement window is stored as its own run, with its own samples, LLM calls, spans and attribution.

```
goal_execution     what was asked: success, number of attempts, total energy
  └─ goal_attempt  one try: outcome, failure cause, start and end time, energy
       └─ runs     one measurement window: energy, power, temperature, phases (about 150 columns)
            └─ energy samples, CPU, thermal, GPU, power rails, LLM calls, spans, attribution (by run_id)
```

Rules:

1. A run is exactly one attempt window. No run value aggregates across attempts.
2. Runs hold what was measured; goals and attempts hold what it meant. Goal columns never appear in runs or sample tables; the link is goal_attempt.run_id.
3. Attempt energy is read from its own run after attribution: goal_attempt.energy_uj equals runs.attributed_energy_uj of that run.
4. Goal energy is the sum of its attempts. If any attempt energy is unknown, the goal total is NULL.
5. Unknown values are NULL, never 0. A measured zero is a valid value.
6. Pre task and post task windows are measured per attempt; they are real setup and teardown work and are never set to zero.
7. Time between attempts (retry decision, recovery, backoff) belongs to no run. It is part of goal wall time, which is not the sum of run durations.

Each attempt window has three parts, all stored on its run:

```
t_pre ── pre task ──▶ t0 ══════ task window ══════ t1 ── post task ──▶ t_post
pre_task_energy_uj        attributed_energy_uj           post_task_energy_uj
framework_overhead_energy_uj = pre_task_energy_uj + post_task_energy_uj
```

## Platform Coverage

| Platform | Architecture | Source | Canonical Role | Confidence | Status |
|---|---|---|---|---|---|
| NVIDIA Grace GB10 | aarch64 | SPBM power channels (energy_samples_v2, energy_sample_domains) | package | 0.92 task window; post window partly inferred | VERIFIED |
| Intel i7-1165G7 | x86_64 | RAPL counters (energy_samples), point reads around the window | package | 0.95 | VERIFIED |
| AMD Ryzen | x86_64 | RAPL (AMD energy driver) | package | to be set | PENDING |
| Apple M1 Pro | arm64 | IOKit energy model | package | to be set | PENDING |

Confidence justification. Intel: RAPL counters bracket the task window exactly and point reads bracket pre and post windows; uncertainty comes from the counter update interval (about 1 ms), negligible for windows above one second. Reaching 1.0 would need an external power meter. GN100: SPBM samples arrive about every 100 ms; the task window is covered by samples and prorated at its edges. The post window lies after sampling stops, so only its first part is measured (the straddling sample, prorated) and the rest is inferred from the power of the nearest samples (method MEASURED_PLUS_NEAREST). If power drops after t1, the inferred part overestimates post energy; for a 250 ms post window at about 86 W this bounds the error by the inferred share (about 70 percent of the post window). Reaching higher confidence needs SPBM sampling through t_post.

## Schema

| Object | Column | Meaning |
|---|---|---|
| goal_attempt | run_id | the run that measured this attempt; one attempt, one run |
| goal_attempt | started_at_ns, finished_at_ns | attempt window, epoch nanoseconds, equal to the run's measured window |
| goal_attempt | energy_uj | attributed energy of its own run |
| goal_execution | total_energy_uj | sum of attempt energies, NULL if any is NULL |
| goal_execution | overhead_energy_uj | energy of attempts that did not succeed |
| runs | pre_task_energy_uj, post_task_energy_uj, framework_overhead_energy_uj | per attempt overhead windows; NULL when not computable |
| energy_attribution | retry_energy_uj | energy of this run's attempt when it is a retry (attempt number above 1), else 0; goal level retry cost lives in goal_execution |
| view v_runs_goal | runs columns plus goal_id, attempt_id, attempt_number, outcome, is_winning | runs with their goal meaning; NULL goal columns for runs without a goal |

Schema version 118 removed the unique rule on runs (exp_id, run_number, workflow_type): several attempts of one repetition now each own a run.

## Method Provenance

| Item | Value |
|---|---|
| method_id | goal_attempt_run_accounting_v1 |
| layer | orchestration |
| fidelity | CALCULATED (sums and links over MEASURED runs); post window on SPBM partly INFERRED |
| formula | $E_{goal} = \sum_{a \in A(goal)} E_{attr}(run(a))$ |
| persistence | all attempts of a goal are measured back to back with no disk I/O between them; at goal end each attempt is saved raw first, then derived steps run; if the goal raises, attempts already measured are saved raw and the original error is raised unchanged |

## Query Reference

**Every run of each goal, with its try number** (all platforms). Expected: one row per attempt; attempt_number 1, 2, ... per goal.

```sql
SELECT goal_id, attempt_number, run_id, outcome, attributed_energy_uj
FROM v_runs_goal
WHERE goal_id IS NOT NULL
ORDER BY run_id DESC LIMIT 6;
```

**Per attempt energy, overhead and sample counts** (aarch64 SPBM shown; on x86_64 replace energy_samples_v2 with energy_samples). Expected: energy_uj equals attr on every row; sample counts above 0 for every attempt.

```sql
SELECT ga.goal_id, ga.attempt_number AS an, ga.run_id,
       ga.energy_uj, r.attributed_energy_uj AS attr,
       r.pre_task_energy_uj, r.post_task_energy_uj, r.framework_overhead_energy_uj AS fw,
       (SELECT COUNT(*) FROM energy_samples_v2 x WHERE x.run_id = r.run_id) AS es2,
       (SELECT COUNT(*) FROM thermal_samples x WHERE x.run_id = r.run_id) AS th
FROM goal_attempt ga JOIN runs r ON r.run_id = ga.run_id
ORDER BY ga.attempt_id DESC LIMIT 6;
```

**Goal total equals the sum of its attempts** (all platforms). Expected: goal_e equals attempts_e.

```sql
SELECT g.goal_id, g.total_energy_uj AS goal_e,
       (SELECT SUM(a.energy_uj) FROM goal_attempt a WHERE a.goal_id = g.goal_id) AS attempts_e
FROM goal_execution g ORDER BY g.goal_id DESC LIMIT 3;
```

## Verification

1. From a sandbox, run a profile with failure injection, for example `alems run example_inject_tool_error`.
2. Run the second query above: each attempt of a multi attempt goal shows its own run_id, energy_uj equals attr, sample counts above 0.
3. Run `alems validate invariants --since <last run before the test>`: INV-A1, INV-A2, INV-E4, INV-E5 pass.

## Known Limitations

- **Hard kill during a goal**: attempts are held in memory until the goal ends; power loss or SIGKILL loses that goal's attempts. Workaround: none; ordinary exceptions are covered.
- **SPBM post window**: partly inferred from nearest sample power (see Platform Coverage). Workaround: compare with Intel post energy; treat GN100 post energy as an upper estimate.
- **Runs stored before schema version 118**: earlier attempts of a retried goal share the final run and have no samples of their own. Workaround: filter analyses to runs after the upgrade (`--since`).
- **Pre attempt failures**: an attempt that failed before its window opened has no run and no energy. Workaround: none; it is counted in goal attempts.
