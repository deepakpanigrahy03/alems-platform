---
**Status:** CURRENT
**Platforms verified:** nvidia_grace
**Last updated:** 2026-10-15
---

# Scheduled Profiles

A profile that contains a `schedule` section runs as a scheduled study: a seeded queue of goal executions, each with a planned failure, a recovery option, an idle check before it, a validity check after it, and one ledger record. A profile without `schedule` runs the plain repetition loop exactly as before.

```bash
cd ~/sandboxes/<sandbox>
alems run <profile-name>                     # profiles/<profile-name>.yaml
alems run <profile-name> --smoke             # first B0 item and the first block of each family
alems run <profile-name> --max-items 3       # stop after 3 queue items
alems run <profile-name> --families a,b      # run only these families of the profile
```

`alems run` takes the profile name, not a path; it searches `profiles/`, `profiles/examples/` and `config/experiment_configs/` and appends `.yaml`.

Run the same command again to resume. Finished queue items are recorded in a state file and skipped, so an interruption loses at most the goal that was running.

## Queue

The queue is built by `core/execution/schedule.py` from the `schedule` section and is identical for identical input.

A block is one task, one failure class, one injection point and one repetition. It holds every recovery option of its family, in an order seeded by the block id. Blocks are shuffled with `seeds.block_order`; B0 items (goals without injection) are inserted at positions drawn from the same generator. With one repetition per point the block id is `task|class|point`; with more it ends in `|r<n>`.

`b0_scope: all` adds B0 items for every task. `b0_scope: checks` adds them only for tasks in a family with turn checks, where they measure the false detection rate.

## Profile Sections

```yaml
study:
  name: raf_e1                       # output folder and ledger names
  experiment_type: failure_injection
  experiment_goal: RAF E1 repetition 1
  workflow_modes: [agentic]
providers:
- {name: vllm_local, model_id: Qwen3-30B-A3B-Instruct-2507-FP8}
execution: {save_db: true}
quality: {enabled: true}
recovery:
  prepare: bfcl                      # alems.harness.prepare plugin
  arms: {escalate: escalate_stub}    # arm name -> alems.harness.deciders plugin
schedule:
  seeds: {block_order: 20261016, policy_order: 20261017, model_seed: 0}
  b0_repetitions: 3
  b0_scope: all
  early_ordinal: 2
  expected_store_suffix: raf-mlsys-2027/experiments.db
  families:
    clean:
      classes: {rate_limit: llm, json_parse: llm, tool_error: tool, timeout: tool}
      options: [FR_cold, FR_warm, SR_cold, SR_warm]
      points: {early: 1, late: 1}    # point -> repetitions
  tasks:
    bfcl_multi_turn_base_64:
      stratum: short
      families: [clean]
      targets: {clean: {late: {llm: 6, tool: 4}}}
isolation:
  level: exclusive
  idle_window_s: 5
  cpu_busy_max_pct: 5.0
  gpu_util_max_pct: 5.0
  retries: 3
  retry_wait_s: 60
validity: {classifier: bfcl_raf, reruns: 1, invalid_cell_max: 0.2}
baseline: {duration_seconds: 10, num_samples: 10, pre_wait_seconds: 30, remeasure: true}
```

| Key | Meaning |
|---|---|
| `families.<f>.classes` | failure class to call type (`llm` or `tool`) |
| `families.<f>.options` | recovery options; `policy@arm` attaches a decider arm |
| `families.<f>.points` | injection points and repetitions per point |
| `families.<f>.max_injections` | firings per goal (2 for persistent classes) |
| `families.<f>.attempts` | attempts per goal (3 for persistent classes) |
| `families.<f>.turn_check` | per turn state check on |
| `tasks.<t>.targets.<f>.<point>` | a number, `{llm: k, tool: k}`, or `{tool_name: name}` (ordinal any) |
| `early_ordinal` | ordinal of every early point of the `clean` family |
| `expected_store_suffix` | refuse to run against any other store |

Recovery options: `FR` restarts from the task prompt and the initial tool state, `SR` resumes at the failed step, `RB` rolls back to the start of the suspect turn. `_warm` keeps the serving engine's prefix cache between attempts, `_cold` resets it.

## What Happens Per Goal

Before the first goal, when `baseline.remeasure` is true, a fresh idle baseline is measured and its id is written to every ledger record.

For each goal, in order: the idle check (CPU busy share and GPU utilization, with retries), `execute_goal` with the pre attempt hook of the `recovery.prepare` plugin, the energy guard (every attempt run must have nonzero package energy, otherwise the run stops), the validity classifier, and one rerun if the goal is invalid. The hook resets the cache, restores tool state and calls a decider; all of it happens before the attempt starts, outside the measurement window.

## Outputs

Next to the store, in `artifacts/<study.name>/`:

| File | Content |
|---|---|
| `<study>_run_state.json` | finished queue item ids (resume) |
| `<study>_run_ledger.jsonl` | one record per goal execution |
| `<study>_smoke_*` | the same for `--smoke` runs |

Ledger fields: `item_id`, `position`, `goal_index`, `plan`, `seeds`, `policy_order`, `idle_check`, `isolation_level`, `rerun`, `goal_id`, `goal_key`, `validity`, `detail`, `family`, `arm`, `attempt_meta` (per attempt injection, corruption, turn checks, tool sequence), `decisions`, `prepare` (resets, restore, cache statistics per attempt), `cache_metrics_after`, `wall_s`, `study`, `profile_sha256`, `engine` (commit and dirty flag), `argv`, `baseline_id`.

The store receives the usual goal, attempt and run rows, plus the recovery data described in [Recovery Data](../developer/recovery-data.md).

## Generating Profiles

Study profiles are generated from committed configurations, never edited by hand:

```bash
cd ~/mydrive/alems-platform
python scripts/raf/make_raf_profiles.py --config scripts/raf/e1_gn100.yaml \
    --name raf_e1 --goal "RAF E1 repetition 1" \
    --provider vllm_local --model Qwen3-30B-A3B-Instruct-2507-FP8 \
    --out ~/sandboxes/raf-mlsys-2027/profiles/raf_e1.yaml
```

A configuration in the pilot form (`policies`, `classes`, `points`, `tasks.<t>.late`) becomes one `clean` family with `b0_scope: all`. A configuration in the family form (from `scripts/raf/select_main_tasks.py`) is copied with `b0_scope: checks`. `scripts/raf/tests/test_raf_profile_equivalence.py` proves that a converted pilot configuration yields the same queue as the pilot driver did.

## Known Limitations

The scheduled runner supports one provider per profile (the first `providers` entry) and the agentic workflow only.

`--smoke` selects one block per family, so a profile with one family smokes two items.

Writes in the scheduled path and in `execute_goal` use the store connection inside a writer transaction, not the writer session API (WCC-2 debt shared with the existing `execute_goal` code).
