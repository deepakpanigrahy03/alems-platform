# Controlled Experiments with the Sequencer

## Overview

`alems run <profile>` runs tasks against providers and stores every run with its energy, GPU, traffic and serving measurements.
That is enough to collect data.
It is not enough to compare conditions fairly, because a measurement machine drifts: it warms up, background load changes, and the first condition after a pause runs on a cooler chip than the next one.

The sequencer runs an experiment as a planned list of steps that controls these effects:

- every treatment is surrounded by its own idle baselines (before and after), so each comparison uses the machine state of that moment;
- the order of conditions is counterbalanced across cycles, so no condition always runs first or last;
- an optional thermal readiness gate waits until the chip is back below a chosen temperature before a step starts;
- every step and every run it produces is recorded so that an interrupted session can be resumed and nothing is ever counted twice.

Use `alems run` for data collection and exploration.
Use `alems sequence` when the result is a comparison between conditions (two engines, two settings, two models, a copy size against idle) that will be reported.

## Concepts

| Term | Meaning |
|---|---|
| Plan | A YAML file listing the conditions to compare and how to run them. |
| Condition | One thing being compared, for example "Mistral on vLLM, gsm8k". |
| Treatment | The step that runs a condition (an `alems run` profile, or a benchmark step from a plugin). |
| Bracket | One treatment with its own pre idle and post idle step. |
| Cycle | One replicate: every condition once, in a counterbalanced order. The cycle is the unit of replication in analysis. |
| Session | One invocation of `alems sequence` for a plan. |
| Attempt | One execution of a step. A retry after an interruption is a new attempt. |
| Gate | The thermal readiness check before a step. |

Samples taken every few milliseconds inside a run (energy, GPU, traffic) describe that run; they never count as extra replicates.

## Writing a plan

A plan has a `sequence` block (settings) and a list of `blocks` (conditions).

```yaml
sequence:
  name: "vLLM vs SGLang, Mistral 7B, gsm8k"
  seed: 17                       # fixes the counterbalanced order
  cycles: 4                      # replicates; use a multiple of the number of conditions
  settle_s: 30                   # minimum wait before every step
  baseline: {kind: idle, duration_s: 60}
  on_baseline_failure: skip_treatment   # or run_treatment
  gate:                          # optional thermal readiness gate
    max_temp_c: 55
    sensor_pattern: "soc|gpu"    # regular expression on sensor names
    max_wait_s: 600
    poll_s: 5
    on_timeout: skip             # or proceed
blocks:
  - condition: vllm_gsm8k
    treatment: {kind: profile, profile: profiles/mistral_vllm_gsm8k.yaml}
  - condition: sglang_gsm8k
    treatment: {kind: profile, profile: profiles/mistral_sglang_gsm8k.yaml}
```

| Key | Required | Meaning | Default |
|---|---|---|---|
| `sequence.name` | no | Label stored with the session | none |
| `sequence.seed` | no | Seed of the counterbalanced order; `--seed` overrides it | none |
| `sequence.cycles` | no | Number of replicates | `ALEMS_SEQ_CYCLES`, else 1 |
| `sequence.settle_s` | no | Minimum wait before every step (seconds) | `ALEMS_SEQ_SETTLE_S`, else 30 |
| `sequence.baseline` | no | The idle step used before and after each treatment | idle, `ALEMS_SEQ_BASELINE_S`, else 60 s |
| `sequence.on_baseline_failure` | no | What happens to a treatment whose pre idle failed | `skip_treatment` |
| `sequence.gate` | no | Thermal readiness gate (see below) | off |
| `blocks[].condition` | yes | Unique name of the condition | |
| `blocks[].treatment.kind` | yes | `profile`, `idle`, or a plugin step kind | |
| `blocks[].treatment.profile` | for `profile` | Path of an `alems run` profile | |
| `blocks[].treatment.args` | no | Extra arguments passed to the profile run | none |
| `blocks[].treatment.settle_s` | no | Settle time for this step only | plan value |

Every default can be set once per machine in `~/.alemsrc` (variables named in the table), so plans stay short and machine independent.

### Profiles used as treatments

A `profile` treatment is an ordinary `alems run` profile.
It must store its runs (the same setting you use for any stored experiment); a profile that stores nothing makes the step fail with reason `no_runs_linked`.
Each treatment creates a normal experiment with its own experiment id, so all existing reports and queries work on it unchanged.

### Order of conditions

With two conditions the cycles alternate AB, BA, AB, BA; the seed decides which comes first.
With more conditions each cycle uses the next row of a Latin square, so every condition takes every position equally often when `cycles` is a multiple of the number of conditions.
The order actually used is stored with the session.

### Thermal readiness gate

After the settle time, the gate reads the platform temperature sensors and waits until the hottest sensor matching `sensor_pattern` is at or below `max_temp_c`, checking every `poll_s` seconds for at most `max_wait_s`.
The first and last observation, the thresholds and the time waited are stored with the step.
If the time runs out, the step is skipped with reason `gate_timeout` (or runs anyway with `on_timeout: proceed`).
On a machine without temperature sensors the gate records `not_available` and the step runs.

## Running

Always check a plan first.
A dry run validates every step (unknown kinds and wrong parameters are reported here, not at three in the morning) and prints the settings and the exact order, without starting any load or writing to the store:

```bash
alems sequence plans/vllm_vs_sglang.yaml --dry-run
```

Run it:

```bash
alems sequence plans/vllm_vs_sglang.yaml
alems sequence plans/vllm_vs_sglang.yaml --seed 23     # different counterbalanced order
```

Only one sequencer can run against a store at a time; a second one stops with `busy`.
Exit codes: 0 completed, 1 stopped (a store write failed), 2 plan error, 3 busy, 130 interrupted.

### Resuming an interrupted session

If the machine reboots or the session is interrupted, continue it:

```bash
alems sequence --resume <session_id>
```

Steps already completed, failed or skipped are not repeated.
A step that was running when the session stopped is closed as `interrupted`; every run it had already stored stays attached to it.
A retried step gets a new attempt, so analysis can choose one attempt per step by a stated rule and never counts an original and its retry as two replicates.

### What happens when something fails

| Event | Result |
|---|---|
| Pre idle fails (or its gate times out) | Treatment and post idle of that bracket are skipped with the reason; the next bracket continues. |
| Treatment fails | Post idle still runs, so the drift is known; the treatment is recorded as failed with its reason and all partial runs. |
| An optional reader (for example GPU traffic) is unavailable | Runs stay valid; the reader records why; the value is empty, never zero. |
| The store cannot be written | The session stops immediately; nothing further runs. |
| Ctrl+C | The current step is marked interrupted; the session can be resumed. |

## Reading the results

All sequencer records live in tables named `energy_validation_seq_*`.

**Sessions.**
```sql
SELECT session_id, name, seed, status, stop_reason, realized_order_json
FROM energy_validation_seq_sessions ORDER BY session_id DESC LIMIT 5;
```

**Every step of a session with its outcome and gate.**
```sql
SELECT s.planned_index, s.cycle_id, s.condition, s.role, s.kind,
       a.attempt_no, a.execution_status, a.reason_code, a.gate_json
FROM energy_validation_seq_steps s
LEFT JOIN energy_validation_seq_attempts a USING (step_id)
WHERE s.session_id = :session
ORDER BY s.planned_index, a.attempt_no;
```

**Runs produced by each treatment, with their energy.**
```sql
SELECT s.cycle_id, s.condition, ar.run_id, ar.workflow_type, r.total_energy_uj
FROM energy_validation_seq_steps s
JOIN energy_validation_seq_attempts a USING (step_id)
JOIN energy_validation_seq_attempt_runs ar USING (attempt_id)
JOIN runs r ON r.run_id = ar.run_id
WHERE s.session_id = :session AND s.role = 'treatment'
ORDER BY s.cycle_id, s.condition, ar.child_seq;
```

**Idle runs around each treatment.** Idle steps are measured runs (experiment type `benchmark`) with the same collectors as the treatment.
```sql
SELECT s.bracket_id, s.role, ar.run_id, r.total_energy_uj, r.duration_ns
FROM energy_validation_seq_steps s
JOIN energy_validation_seq_attempts a USING (step_id)
JOIN energy_validation_seq_attempt_runs ar USING (attempt_id)
JOIN runs r ON r.run_id = ar.run_id
WHERE s.session_id = :session AND s.role IN ('pre', 'post')
ORDER BY s.planned_index;
```

## Reporting in a paper

- Report gross energy of each treatment as measured.
  The sequencer subtracts nothing.
- If you report energy above idle, state the estimator.
  The recommended one: mean idle power of the accepted pre and post baseline, interpolated linearly over the treatment window, integrated over the exact treatment interval, in the same energy domain.
  Also report the result with the pre baseline only, as a sensitivity check; a treatment that heats the chip raises its own post baseline.
- The replicate count is the number of cycles with eligible brackets, not the number of samples or runs.
- Report how many brackets were planned, executed, eligible and excluded, per condition and reason.

## Known limitations

- **Device wide counters**: energy and GPU traffic include everything on the machine. Run on a dedicated machine and declare it in the experiment.
- **Profile envelope**: a treatment that runs several runs is measured as the envelope of those runs; gaps between them are not part of any run.
- **Readiness**: the gate covers temperature only; idle power stability checks are not yet automatic.
- **Remote servers**: when the serving engine runs on another machine, its process state cannot be verified from the measuring machine.

## Kernel Benchmark over Memory Placements (kernel_rw)

The `kernel_rw` step kind measures the energy a GPU kernel spends to read or write a known amount of data, with the buffer allocated in one of four ways. The workload is `pravaha_mem.known_kernel` (pravaha 1.1.0 or later), so A-LEMS holds no CUDA code.

**What is controlled.**

| Parameter | Values | Meaning |
|---|---|---|
| `placement` | device, pinned, managed, pageable | allocation API: `cudaMalloc`, `cudaMallocHost`, `cudaMallocManaged`, `aligned_alloc` (pageable needs the GPU's pageable memory access, for example ATS on GB10) |
| `prefetch` | none, device | managed only (required there); stored as not_applicable otherwise |
| `op` | read, write | read sums the buffer (one 8 byte partial sum per block, stored as aux_write_bytes); write stores a constant |
| `init` | gpu, cpu | who initialises the buffer before the window (default `ALEMS_KRW_INIT`, gpu) |
| `size_mib`, `duration_s` | from `ALEMS_KRW_SIZES_MIB`; seconds | the step is sized from a probe batch to last the duration |
| `control` | true | held window of the same duration, buffer prepared, no passes (logical bytes 0) |

**What is observed.** Energy of the measured run; device wide traffic at the pravaha counters over the work window (`observed_read_miss_bytes`, `observed_write_bytes`, `observed_per_logical_byte`, `traffic_steadiness`); memory attributes before and after the work. The attributes are requests and policy (last prefetch, preferred location), not where pages are, so `residency_changed` is stored as NULL. Placement is reported as allocation API and prefetch policy, never as a path such as C2C without traffic evidence.

**The checksum.** Every run checks its result outside the window. A wrong checksum fails the step with `check_failed`; a failed step is never a zero energy measurement.

**Primary outcome (analysis).** Incremental joules per logical byte by energy domain: treatment energy minus the bracket idle, divided by `logical_bytes`. Observed traffic is reported next to it and never used as the denominator, because a small buffer read repeatedly is mostly served by the cache: its observed miss bytes are far below its logical bytes, which is itself a result.

**Example plan.** `config/sequence_plans/example_placements.yaml`: 10 treatments and 5 held controls at 16 MiB.

**Kernel characterisation.** After the session, `alems sequence --kernel-pass SESSION_ID` runs one Nsight Compute pass per condition (method `kernel_traffic_ncu_v1`) with a short run of the same program, matched to the first completed treatment run of that condition. Launch counts differ, so agreement is `scope_mismatch` with a NULL ratio by design; the pass gives per launch read and write sectors and shows setup kernels.

```sql
SELECT s.condition, MAX(CASE WHEN r.key='logical_bytes' THEN r.value END) AS logical,
       MAX(CASE WHEN r.key='observed_per_logical_byte' THEN r.value END) AS observed_per_logical,
       MAX(CASE WHEN r.key='traffic_steadiness' THEN r.value END) AS steadiness
FROM energy_validation_seq_steps s
JOIN energy_validation_seq_attempts a USING (step_id)
JOIN energy_validation_seq_step_results r USING (attempt_id)
WHERE s.session_id = :session AND s.kind = 'kernel_rw'
GROUP BY s.planned_index ORDER BY s.planned_index;
```

## Kernel Benchmark over Memory Placements (kernel_rw)

The `kernel_rw` step kind measures the energy a GPU kernel spends to read or write a known amount of data, with the buffer allocated in one of four ways. The workload is `pravaha_mem.known_kernel` (pravaha 1.1.0 or later), so A-LEMS holds no CUDA code.

**What is controlled.**

| Parameter | Values | Meaning |
|---|---|---|
| `placement` | device, pinned, managed, pageable | allocation API: `cudaMalloc`, `cudaMallocHost`, `cudaMallocManaged`, `aligned_alloc` (pageable needs the GPU's pageable memory access, for example ATS on GB10) |
| `prefetch` | none, device | managed only (required there); stored as not_applicable otherwise |
| `op` | read, write | read sums the buffer (one 8 byte partial sum per block, stored as aux_write_bytes); write stores a constant |
| `init` | gpu, cpu | who initialises the buffer before the window (default `ALEMS_KRW_INIT`, gpu) |
| `size_mib`, `duration_s` | from `ALEMS_KRW_SIZES_MIB`; seconds | the step is sized from a probe batch to last the duration |
| `control` | true | held window of the same duration, buffer prepared, no passes (logical bytes 0) |

**What is observed.** Energy of the measured run; device wide traffic at the pravaha counters over the work window (`observed_read_miss_bytes`, `observed_write_bytes`, `observed_per_logical_byte`, `traffic_steadiness`); memory attributes before and after the work. The attributes are requests and policy (last prefetch, preferred location), not where pages are, so `residency_changed` is stored as NULL. Placement is reported as allocation API and prefetch policy, never as a path such as C2C without traffic evidence.

**The checksum.** Every run checks its result outside the window. A wrong checksum fails the step with `check_failed`; a failed step is never a zero energy measurement.

**Primary outcome (analysis).** Incremental joules per logical byte by energy domain: treatment energy minus the bracket idle, divided by `logical_bytes`. Observed traffic is reported next to it and never used as the denominator, because a small buffer read repeatedly is mostly served by the cache: its observed miss bytes are far below its logical bytes, which is itself a result.

**Example plan.** `config/sequence_plans/example_placements.yaml`: 10 treatments and 5 held controls at 16 MiB.

**Kernel characterisation.** After the session, `alems sequence --kernel-pass SESSION_ID` runs one Nsight Compute pass per condition (method `kernel_traffic_ncu_v1`) with a short run of the same program, matched to the first completed treatment run of that condition. Launch counts differ, so agreement is `scope_mismatch` with a NULL ratio by design; the pass gives per launch read and write sectors and shows setup kernels.

```sql
SELECT s.condition, MAX(CASE WHEN r.key='logical_bytes' THEN r.value END) AS logical,
       MAX(CASE WHEN r.key='observed_per_logical_byte' THEN r.value END) AS observed_per_logical,
       MAX(CASE WHEN r.key='traffic_steadiness' THEN r.value END) AS steadiness
FROM energy_validation_seq_steps s
JOIN energy_validation_seq_attempts a USING (step_id)
JOIN energy_validation_seq_step_results r USING (attempt_id)
WHERE s.session_id = :session AND s.kind = 'kernel_rw'
GROUP BY s.planned_index ORDER BY s.planned_index;
```
