---
**Method ID:** none (scheduling only; no calculated value is stored)
**Schema version:** thermal_exposure e007 (te_session.pacing_params_json)
**Platforms verified:** NVIDIA Grace GB10 (aarch64)
**Status:** REVIEW
**Last updated:** 2026-11-26
---

# Session Plans

## Overview

A session plan decides, step by step, what an experiment does inside one continuous recording:
which goal runs next, with which workflow, and how long to wait before the next one. Without a
plan an experiment runs its fixed loop over tasks, providers and repetitions, unchanged. With a
`session_plan` block in the experiment profile, a plan loop replaces that loop for the call.

Each step is one of four kinds: a goal (task and workflow, run through the normal goal path with
retries, scoring and persistence), a load step (a goal held for a set time), a wait, or done. After
every goal the plan receives the outcome (success, attempt and run ids, start and end times) and
chooses the next step. The safety guard still gates every run: a plan never shortens the guard's
rest, and guard waits are never counted as plan pauses.

Plans are plugins in the entry point group `alems.harness.pacing`. The thermal extension provides
five:

| Plan | What it does |
| --- | --- |
| `back_to_back` | Each goal starts as soon as the previous one ends (guard rest only) |
| `fixed_pause` | A pause of `gap_factor × tau_s` after each goal, except the last |
| `matched_throughput` | Pauses chosen so the session completes goals at a target rate; a negative computed pause refuses the profile before anything starts |
| `linear_first` | The linear workflow first; the agentic workflow only when the linear goal failed |
| `calibration_steps` | A load task held for `hold_s`, then a rest of `rest_s`, repeated |

## Session Plan Rules { #session-plan-rules }

| Rule | What happens |
| --- | --- |
| Fixed loop unchanged | A profile without `session_plan` runs exactly as before |
| Checked before start | `alems thermal run --profile <file> --dry-run` checks the parameters and lists the planned steps without running anything; a real run repeats the same checks first |
| Order | Goal order comes from `goals`, `workflows`, `repeats` and `seed`; the same profile always gives the same order |
| No pause after the last goal | The recording's cooling tail covers the time after the last goal |
| Cooling constant | `tau_s` always carries `tau_status` (PROVISIONAL, PILOT_ONLY or MEASURED) and `tau_source`; a provisional value is never presented as measured |
| Guard first | A planned pause is a minimum: the actual gap is the planned pause or the guard's wait, whichever is longer; both are recorded |
| Labels | With a recording open, each plan writes `goal_slot` (per goal; for `linear_first` one slot for the linear goal and the agent goal that followed it), `pause` (per wait) and `calibration_step` (per hold) spans under the workload span; labels never move run boundaries |
| Profile identity | The recording stores the profile name, the SHA-256 of the profile file, the plan name and its parameters |

A sequencer plan can run a thermal profile as one step with the step kind `thermal_session`. It runs
the same checks when the sequencer plan is loaded, then the session; every goal run is linked to the
sequencer attempt.

## Platform Coverage

| Platform | Architecture | Source | Canonical Role | Confidence | Status |
|----------|-------------|--------|---------------|------------|--------|
| NVIDIA Grace GB10 | aarch64 | goal path and continuous recording | scheduling | n/a | VERIFIED |
| Intel x86_64 | x86_64 | goal path and continuous recording | scheduling | n/a | PENDING |
| Apple M4 | arm64 | goal path and continuous recording | scheduling | n/a | PENDING |
| AMD Ryzen | x86_64 | goal path and continuous recording | scheduling | n/a | PENDING |

Plans contain no platform code; the status follows the continuous recording on each platform.

## Schema

| Table | One row per | Columns used by plans |
| --- | --- | --- |
| `te_session` | recording | `profile_id` (profile file name without extension), `profile_sha256`, `pacing_plugin` (plan name), `pacing_params_json` (plan parameters, keys sorted; NULL without a plan) |
| `spans` (vocabulary `thermal_session`) | labelled interval | kinds `goal_slot`, `pause`, `calibration_step` under `workload` |
| `span_attributes` | span attribute | `goal_slot`: goal_id, workflow, attempt_run_ids, success, agent_attempt; `pause`: planned_s, actual_s, rule; `calibration_step`: channel, level, hold_s, hold_achieved, settled |

Goals and their runs stay in `goal_execution`, `goal_attempt` and `runs`, as without a plan.
`settled` on a calibration step is left NULL by the plan; the calibration analysis decides it.

## Method Provenance

| Field | Value |
| --- | --- |
| method_id | none |
| provenance | not applicable (scheduling decisions, not measurements) |
| layer | experiment design |
| confidence | not applicable |

Plans store decisions and the parameters behind them. Planned pauses are stored next to the actual
gaps, so analysis can always use what happened rather than what was planned.

## Query Reference

**Which profile and plan produced each recording.** Answers: was this recording made with a session
plan, which profile file (by hash), and with which parameters? Applies to every platform. One row per
recording; plan columns are NULL or the defaults for recordings made without a plan.

```sql
SELECT session_id, profile_id, pacing_plugin, substr(profile_sha256, 1, 12) AS sha12,
       pacing_params_json
FROM te_session
ORDER BY session_id;
```

**Plan labels in the latest recording.** Answers: how many goal slots, pauses and calibration steps
did the plan write? Typical pilot values: 2 to 4 goal slots, 1 to 3 pauses.

```sql
SELECT kind, COUNT(*) AS n
FROM spans
WHERE trace_id = (SELECT global_session_id FROM te_session ORDER BY session_id DESC LIMIT 1)
GROUP BY kind
ORDER BY kind;
```

## Verification

1. Dry run: `alems thermal run --profile <profile> --dry-run` lists the steps and exits 0; a profile
   with a bad parameter exits 2 with one message.
2. Unit tests: `python -m pytest -q tests/test_session_plan.py tests/test_pacing_plans.py tests/test_thermal_session_step.py` passes.
3. One real session on a sandbox store, then both queries above: the plan name and parameters are
   stored, and the label counts match the dry run (one goal slot per goal or linear-first slot, one
   pause per wait).
4. `python scripts/test_exp_integrity.py --exp-id <id>` reports no failures for the session.

## Known Limitations

- **Provisional cooling constant**: `tau_s` is a provisional or pilot value until it is measured.
  Workaround: none — its status and source are stored with every recording.
- **Calibration load task**: the load task for calibration steps is not yet chosen; pilots use a
  short calculation task, so holds may end early (`hold_achieved` false).
  Workaround: none — read `hold_achieved` before using a step.
- **Sequencer brackets**: the sequencer runs idle measurement runs before and after every treatment
  step, including `thermal_session`, in addition to the session's own idle window.
  Workaround: account for the extra idle runs when analysing sequencer-driven sessions.
- **Absolute profile paths**: from versions before relative-path support, profiles must be given with
  a full path.
  Workaround: give the full path.
