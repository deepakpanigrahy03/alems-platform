# A-LEMS Failure Injection Framework
## Researcher Guide v1.0

**Platform:** A-LEMS (Agentic LLM Energy Measurement System)
**Schema version:** v100
**Verified on:** GN100 (NVIDIA Grace GB10, aarch64), UBUNTU2505 (Intel x86_64)
**Date:** September 2026

---

## Table of Contents

1. Overview
2. Failure Taxonomy Reference
3. Injectable vs Detectable Types
4. Experiment Configuration Reference
5. Example Experiments — Injectable Types
6. Example Experiments — Detectable Types
7. Verified Energy Measurements (GN100 Baseline)
8. Database Tables and Trace Path
9. Interpreting Results
10. Designing Your Study
11. Verification and Validation
12. Known Limitations
13. Troubleshooting
14. Extending the Framework

---

## 1. Overview

The A-LEMS Failure Injection Framework enables controlled, reproducible,
energy-measurable failure studies for agentic AI systems. The scientific
thesis is that each failure class has a distinct energy signature — measurable
at the hardware level via SPBM/RAPL — and that signature determines the true
cost of that failure class including wasted inference energy and retry overhead.

### What the framework measures

For each failure type it measures:

- **Wasted energy** — energy consumed by the failed attempt
- **Retry energy** — energy consumed by the recovery attempt
- **Overhead ratio** — wasted / successful energy
- **Phase breakdown** — where in the pipeline (planning/execution/synthesis) the energy was consumed
- **Retry amplification** — how much more energy the retry consumed vs the failed attempt

### Two measurement paths

**Injectable types** use `ScenarioInjector` to deterministically trigger
infrastructure failures (tool errors, timeouts, network failures). The LLM
runs for real. Energy is always hardware-measured. The injection controls
which failure path the retry machinery takes.

**Detectable types** use real LLM runs with `HallucinationDetector` and
`QualityJudge`. The LLM produces natural failures. Energy of detected
failure runs is measured and compared to successful runs.

### Why this matters for papers

Paper 8 (Failure-Class-Aware Retry Policies) needs per-type energy cost
profiles across all 14 failure classes. Without this framework, you would
need to wait for natural failures to occur at sufficient volume. With the
framework, you can produce statistically significant energy measurements for
any failure type in hours, not weeks.

---

## 2. Failure Taxonomy Reference

The taxonomy lives in the `failure_taxonomy` table. As of schema v098 there
are 14 canonical entries. Adding a new type requires one INSERT — no code
changes, no migrations.

### Complete taxonomy

| failure_type_id | domain | retryable | cost_rank | default_recovery |
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

### Domain groupings

**reasoning** — LLM-produced errors. Cannot be injected. Measured via
`HallucinationDetector` on real LLM runs.

**execution** — Infrastructure failures during tool execution. Injectable.

**communication** — Network and API failures. Injectable.

**validation** — Parse and format failures. Injectable.

**platform_specific** — Platform-specific types added by researchers.
Use `domain='platform_specific'` when inserting custom types.

### Query the taxonomy

```sql
SELECT failure_type_id, domain, default_retryable, typical_cost_rank,
       default_recovery_strategy
FROM failure_taxonomy
ORDER BY typical_cost_rank;
```

---

## 3. Injectable vs Detectable Types

### Injectable types (9)

These types are simulated by `ScenarioInjector` returning a controlled
`ToolResult(success=False)` or post-harness result dict. The LLM executes
fully on every attempt — energy is always real. The injection controls which
failure path the retry machinery takes.

| Type | Injection point | Retry behavior |
|---|---|---|
| tool_error | Inside _dispatch_tool | Retried (retry_on_tool_error) |
| timeout | Post-harness result dict | Retried (retry_on_timeout) |
| api_error | Inside _dispatch_tool | Retried (retry_on_api_error) |
| network_error | Inside _dispatch_tool | Retried (retry_on_api_error) |
| rate_limit | Inside _dispatch_tool | Retried (retry_on_api_error) |
| malformed_output | Inside _dispatch_tool | Retried (retry_on_tool_error) |
| json_parse | Inside _dispatch_tool | Retried (retry_on_tool_error) |
| not_found | Inside _dispatch_tool | NOT retried (abort) |
| auth_error | Inside _dispatch_tool | NOT retried (abort) |

### Detectable types (3)

These types are measured from real LLM runs. You cannot inject them.
`HallucinationDetector` classifies failed quality judgments into these types.

| Type | Detection method | Score threshold |
|---|---|---|
| hallucination | llm_judge or exact_match | normalized_score < 0.3 |
| semantic_error | semantic scorer | 0.3 ≤ score < 0.5 |
| capability_error | exact_match | score = 0.0 |

### The scientific distinction

A hallucination is not just a wrong answer. The LLM internally generates
more tokens, backtracks, over-generates. That process consumes measurably
more energy than a clean correct answer. You cannot simulate this by
relabeling a result — the energy signature comes from what the model
actually computed, not from how you classify it.

---

## 4. Experiment Configuration Reference

### Full YAML schema

```yaml
study:
  name: "Your study name"
  experiment_type: "failure_injection"     # required for injection to activate
  experiment_goal: "Research question"
  workflow_modes: ["agentic"]              # linear | agentic | both

tasks:
  - id: tg_single_calc                    # tool-graph task for injection
  - id: gsm8k_basic                       # QA task for detection

providers:
  - name: vllm_remote                     # use capable model for injection
    model_id: Mistral-7B-Instruct-v0.3   # tinyllama hallucinates past failures

execution:
  repetitions: 10                         # reps per task/provider combination
  cool_down_seconds: 10
  save_db: true

retry_policy:
  name: "default"
  max_retries: 3                          # must be > 0 for execute_goal path
  retry_on_timeout: true
  retry_on_tool_error: true
  retry_on_api_error: true
  retry_on_wrong_answer: false
  backoff_seconds: 1.0

failure_injection:
  enabled: true
  mode: scenario                          # scenario | deterministic_validation |
                                          # controlled_retry | statistical |
                                          # deterministic_stress
  scenario_id: "your_study_id_v1"        # unique provenance key
  dry_run: false                          # true = evaluate rules, no actual injection
  scenarios:
    - type: tool_error                    # failure_taxonomy.failure_type_id
      rate: 1.0                           # Bernoulli probability 0.0 to 1.0
      location:
        phase: execution                  # planning | execution | synthesis |
                                          # post_harness | any
        step_index: any                   # integer | "N-M" range | any
        tool_name: any                    # specific tool name | any
      max_injections: 1                   # integer | unlimited

quality:
  enabled: false                          # true for detection experiments
```

### Critical configuration rules

**`experiment_type: failure_injection`** — required. Without this, the
injection engine is disabled regardless of other settings.

**`max_retries > 0`** — required for the `execute_goal` path which owns
energy measurement. Setting `max_retries: 0` routes to `save_pair` which
has no injection support.

**Use `vllm_remote`** — capable models (Mistral 7B, Llama 70B) correctly
report tool failures and trigger retry. Small models (TinyLlama 1B)
hallucinate past failures and show `outcome=success` even when injection fired.

**`scenario_id`** — set a unique, versioned ID for every study. This key
appears in `failure_injection_log` and is your provenance anchor for
reproducing results. Use format `study_name_vN`.

### Mode reference

| mode | Use case | Randomness |
|---|---|---|
| scenario | Per-type energy profiling, Paper 8 | Seeded Bernoulli per rule |
| deterministic_validation | CI, architecture validation | None — evenly spaced slots |
| controlled_retry | Select N% of goals for injection | None |
| statistical | Large-scale statistical studies | SHA-256 seeded |
| deterministic_stress | Full retry exhaustion path | None — always inject |

### Location matching

The `location:` block in each scenario rule controls where in the trajectory
injection fires:

```yaml
location:
  phase: execution        # which execution phase
  step_index: 2           # specific step (0-indexed), range "1-3", or "any"
  tool_name: database_query  # specific tool or "any"
```

If `phase: post_harness`, injection fires after the harness completes and
overwrites the result dict. Use this for `timeout` and `rate_limit` at the
request level. All other types use `phase: execution`.

---

## 5. Example Experiments — Injectable Types

All examples verified on GN100 with `vllm_remote/Mistral-7B-Instruct-v0.3`.
Copy these to your experiment workspace and set your own `scenario_id`.

### 5.1 tool_error

```yaml
# config/experiment_configs/examples/example_inject_tool_error.yaml
study:
  name: "Example: tool_error injection"
  experiment_type: "failure_injection"
  experiment_goal: "Verify tool_error injection path and measure retry energy"
  workflow_modes: ["agentic", "linear"]

tasks:
  - id: tg_single_calc

providers:
  - name: vllm_remote
    model_id: Mistral-7B-Instruct-v0.3

execution:
  repetitions: 1
  save_db: true

retry_policy:
  name: "default"
  max_retries: 3
  retry_on_tool_error: true
  backoff_seconds: 1.0

failure_injection:
  enabled: true
  mode: scenario
  scenario_id: "example_tool_error_v1"
  scenarios:
    - type: tool_error
      rate: 1.0
      location:
        phase: execution
        step_index: any
        tool_name: any
      max_injections: 1

quality:
  enabled: false
```

**Verified output (GN100):**

```
vllm_remote  Single Calculator    51.16J    18.69J    0.37x
```

**DB trace:**

```
goal_attempt: attempt 1 | failure | tool_error | 46.36J
goal_attempt: attempt 2 | success |            | 18.69J
failure_injection_log: injected | tool_error | execution | seed=6abb4cabb0e1c511
failure_injection_log: eligible |            | post_harness | location_mismatch
```

**What this measures:** Energy wasted when a tool call fails and the LLM
must retry the entire goal. Wasted fraction = 46.4J / 18.7J = 2.48x overhead.

### 5.2 timeout

```yaml
failure_injection:
  enabled: true
  mode: scenario
  scenario_id: "example_timeout_v1"
  scenarios:
    - type: timeout
      rate: 1.0
      location:
        phase: post_harness
        step_index: any
        tool_name: any
      max_injections: 1
```

**Verified output (GN100):**

```
attempt 1: failure | timeout | 97.01J
attempt 2: success |         | 112.26J
```

**Note:** timeout fires post-harness. The LLM completes fully (energy
captured), then the result is overwritten with timeout status. Retry
energy (112J) is higher than failed energy (97J) in this run — the retry
took longer due to a more verbose synthesis response.

### 5.3 api_error

```yaml
failure_injection:
  enabled: true
  mode: scenario
  scenario_id: "example_api_error_v1"
  scenarios:
    - type: api_error
      rate: 1.0
      location:
        phase: execution
        step_index: any
        tool_name: any
      max_injections: 1
```

**Verified output (GN100):**

```
attempt 1: failure | api_error | 54.31J
attempt 2: success |           | 28.34J
```

### 5.4 network_error

```yaml
scenarios:
  - type: network_error
    rate: 1.0
    location: {phase: execution, step_index: any, tool_name: any}
    max_injections: 1
```

**Verified output (GN100):**

```
attempt 1: failure | network_error | 58.04J
attempt 2: success |               | 17.18J
```

**Note:** `network_error` maps to `api_error` in the retry coordinator
(both are communication-domain failures handled by `retry_on_api_error`).
`failure_injection_log.injected_type` records `network_error` for
provenance. `goal_attempt.failure_type` records `network_error` after
the regex classifier extracts the type from `INJECTED[network_error]:` prefix.

### 5.5 rate_limit

```yaml
scenarios:
  - type: rate_limit
    rate: 1.0
    location: {phase: execution, step_index: any, tool_name: any}
    max_injections: 1
```

**Verified output (GN100):**

```
attempt 1: failure | rate_limit | 52.43J
attempt 2: success |            | 27.08J
```

### 5.6 malformed_output

```yaml
scenarios:
  - type: malformed_output
    rate: 1.0
    location: {phase: execution, step_index: any, tool_name: any}
    max_injections: 1
```

**Verified output (GN100):**

```
attempt 1: failure | malformed_output | 63.98J
attempt 2: success |                  | 22.91J
```

**Note:** malformed_output simulates a tool that returns syntactically
broken data. The failure is detected via step_errors scanning. A capable
model (Mistral 7B) correctly reports the failure and triggers retry. Weak
models may synthesize past the error — always use a capable model.

### 5.7 json_parse

```yaml
scenarios:
  - type: json_parse
    rate: 1.0
    location: {phase: execution, step_index: any, tool_name: any}
    max_injections: 1
```

**Verified output (GN100):**

```
attempt 1: failure | json_parse | 52.44J
attempt 2: success |            | 12.24J
```

### 5.8 not_found (non-retryable)

```yaml
retry_policy:
  name: "no_retry"
  max_retries: 3           # max_retries must still be > 0 for execute_goal path
  retry_on_tool_error: false

scenarios:
  - type: not_found
    rate: 1.0
    location: {phase: execution, step_index: any, tool_name: any}
    max_injections: 1
```

**Verified output (GN100):**

```
attempt 1: failure | not_found | 56.36J
(no attempt 2 — non-retryable, goal aborts)
```

**What this measures:** Minimum failure cost — one attempt, no retry.
Compare against retryable types to quantify the value of retry policies.

### 5.9 auth_error (non-retryable)

```yaml
scenarios:
  - type: auth_error
    rate: 1.0
    location: {phase: execution, step_index: any, tool_name: any}
    max_injections: 1
```

**Verified output (GN100):**

```
attempt 1: failure | auth_error | 74.58J
(no attempt 2 — non-retryable, goal aborts)
```

---

## 6. Example Experiments — Detectable Types

Detectable types require real LLM runs with quality judging enabled.
Use tasks with `expected_answer` fields. Small models (TinyLlama 1B)
naturally produce failures on these tasks due to limited capability.

### 6.1 hallucination detection

```yaml
study:
  name: "Example: hallucination detection"
  experiment_type: "normal"              # NOT failure_injection
  workflow_modes: ["linear"]

tasks:
  - id: factual_qa
  - id: science_qa
  - id: geography_qa

providers:
  - name: llama_cpp
    model_id: tinyllama-1b-gguf          # small model for natural failures

execution:
  repetitions: 20
  save_db: true

retry_policy:
  name: "no_retry"
  max_retries: 1                         # still need > 0 for execute_goal path

quality:
  enabled: true
  judge_method: exact_match
```

**What to check after run:**

```sql
SELECT hallucination_type, detection_method, severity, COUNT(*)
FROM hallucination_events
WHERE detected_at > datetime('now','-1 hour')
GROUP BY hallucination_type, detection_method, severity;
```

Expected: rows with `hallucination_type=hallucination`, `detection_method=exact_match`.

### 6.2 semantic_error detection

```yaml
tasks:
  - id: gsm8k_basic
  - id: gsm8k_multi_step
  - id: logical_reasoning

quality:
  enabled: true
  judge_method: semantic                 # graded scores, not binary
```

**What to check:**

```sql
SELECT normalized_score, pass_fail FROM output_quality
WHERE created_at > datetime('now','-1 hour')
  AND normalized_score BETWEEN 0.3 AND 0.5;
```

### 6.3 capability_error detection

```yaml
tasks:
  - id: code_fibonacci
  - id: code_sorting
  - id: bug_fixing

quality:
  enabled: true
  judge_method: exact_match             # binary — model either produces code or not
```

**What to check:**

```sql
SELECT hallucination_type, normalized_score FROM hallucination_events
WHERE detected_at > datetime('now','-1 hour')
  AND hallucination_type = 'capability_error';
```

---

## 7. Verified Energy Measurements (GN100 Baseline)

All values measured on NVIDIA Grace GB10 (aarch64) with
vllm_remote/Mistral-7B-Instruct-v0.3 on `tg_single_calc` task.
Two repetitions per type. Energy in Joules.

| failure_type | failed_energy_j | retry_energy_j | overhead_ratio | retryable |
|---|---|---|---|---|
| tool_error | 56.2 / 43.5 | 25.3 / 21.0 | 2.48x | YES |
| timeout | 97.0 / 91.1 | 112.3 / 87.3 | 1.08x | YES |
| api_error | 54.3 / 51.3 | 28.3 / 16.7 | 2.07x | YES |
| network_error | 58.0 / 38.7 | 17.2 / 10.7 | 3.08x | YES |
| rate_limit | 52.4 / 41.8 | 27.1 / 10.4 | 2.27x | YES |
| malformed_output | 64.0 / 53.8 | 22.9 / 16.3 | 2.83x | YES |
| json_parse | 52.4 / 48.7 | 12.2 / 14.7 | 3.73x | YES |
| not_found | 56.4 / 44.2 | none | 1.0x (abort) | NO |
| auth_error | 74.6 / 43.3 | none | 1.0x (abort) | NO |

**Reading the table:** `overhead_ratio` is `failed_energy / retry_energy`.
A ratio of 2.48x for `tool_error` means the failed attempt consumed 2.48
times more energy than the successful retry. This is the wasted energy
that a smarter retry policy could save.

**Key finding:** Non-retryable types (not_found, auth_error) have the
lowest total energy cost despite having no recovery — they abort immediately.
Retryable types with high overhead ratios (json_parse 3.73x, network_error
3.08x) are the most expensive class to recover from.

---

## 8. Database Tables and Trace Path

### Primary trace query

Start every analysis with `exp_id`. Find it in `experiments` table by
`created_at` or `experiment_goal`.

```sql
SELECT
    e.exp_id,
    e.experiment_goal,
    e.experiment_type,
    r.run_id,
    r.workflow_type,
    ROUND(r.pkg_energy_uj / 1e6, 3)     AS pkg_energy_j,
    ROUND(r.dynamic_energy_uj / 1e6, 3) AS dynamic_energy_j,
    ge.goal_id,
    ge.success,
    ge.total_attempts,
    ga.attempt_number,
    ga.outcome,
    ga.failure_type,
    ROUND(ga.energy_uj / 1e6, 3)        AS attempt_energy_j,
    fil.status                           AS injection_status,
    fil.injected_type,
    fil.random_seed
FROM experiments e
JOIN goal_execution ge ON ge.exp_id = e.exp_id
JOIN goal_attempt ga   ON ga.goal_id = ge.goal_id
JOIN runs r            ON r.run_id = ga.run_id
LEFT JOIN failure_injection_log fil ON fil.attempt_id = ga.attempt_id
WHERE e.exp_id = ?
ORDER BY ge.goal_id, ga.attempt_number, fil.injection_id;
```

### Table reference

**`experiments`** — one row per experiment run. `exp_id` is the root key.

**`goal_execution`** — one row per user goal. Links to `experiments` via
`exp_id`. `success`, `total_attempts`, `winning_attempt_id`.

**`goal_attempt`** — one row per execution attempt. Links to `goal_execution`
via `goal_id`. `outcome` (success/failure/partial), `failure_type` (taxonomy
type), `energy_uj` (RAPL-measured energy for this attempt).

**`failure_injection_log`** — one row per injection decision (injected,
skipped, suppressed, eligible, target_not_reached). Links to `goal_attempt`
via `attempt_id`. `scenario_id`, `injected_type`, `random_seed`, `status`.

**`tool_failure_events`** — one row per recorded tool failure. `failure_type`,
`retry_attempted`, `retry_success`, `wasted_energy_uj` (ETL-populated).

**`runs`** — one row per winning run. Full energy breakdown including
`pkg_energy_uj`, `dynamic_energy_uj`, `pre_task_energy_uj`,
`post_task_energy_uj`. Links to `goal_attempt` via `run_id`.

**`hallucination_events`** — one row per detected hallucination.
`hallucination_type` (hallucination/semantic_error/capability_error),
`detection_method`, `severity`, `wasted_energy_uj`.

**`output_quality`** — one row per scored attempt. `normalized_score`,
`pass_fail`, `score_method`.

**`goal_output`** — raw LLM response text per attempt.

### Injection log status values

| status | meaning |
|---|---|
| injected | Rule matched, draw passed, failure triggered |
| selected | dry_run=true: rule would have fired |
| skipped | Draw failed (random value >= rate) |
| eligible | Location did not match rule |
| suppressed | max_injections already reached for this goal |
| target_not_reached | Rule targeted a step beyond actual trajectory |

### Cross-type energy analysis (Paper 8 core query)

```sql
SELECT
    ga.failure_type,
    ft.domain,
    ft.default_retryable,
    ft.typical_cost_rank,
    COUNT(*)                                    AS failed_attempts,
    ROUND(AVG(ga.energy_uj) / 1e6, 2)          AS mean_failed_energy_j,
    ROUND(MIN(ga.energy_uj) / 1e6, 2)          AS min_failed_energy_j,
    ROUND(MAX(ga.energy_uj) / 1e6, 2)          AS max_failed_energy_j
FROM goal_attempt ga
JOIN failure_taxonomy ft ON ga.failure_type = ft.failure_type_id
WHERE ga.outcome = 'failure'
  AND ga.energy_uj > 0
GROUP BY ga.failure_type
ORDER BY ft.typical_cost_rank;
```

---

## 9. Interpreting Results

### Reading the master summary

```
Provider     Task              Linear (J)  Agentic (J)    Tax (x)
vllm_remote  Single Calculator   51.16      18.69          0.37x
```

For injection experiments `Agentic (J)` shows total goal energy including
all attempts (failed + successful). `Linear (J)` shows linear baseline if
`workflow_modes: ["agentic", "linear"]` is set. `Tax (x)` is
`agentic / linear` — only meaningful when both sides run.

### Reading goal_attempt energy

```
attempt 1: failure | tool_error | 46.36J  ← wasted energy
attempt 2: success |            | 18.69J  ← successful energy
```

**Wasted energy** = energy of all failed attempts for this goal.
**Successful energy** = energy of the winning attempt.
**Overhead ratio** = wasted / successful.

### Reading failure_injection_log

```
injected | tool_error | execution | seed=6abb4cabb0e1c511  ← injection fired
eligible |            | post_harness | location_mismatch   ← post-harness check, no match
suppressed | tool_error | execution |                      ← max_injections reached on retry
```

The `eligible` row with `location_mismatch` at `post_harness` appears for
every agentic run — it is the post-harness timeout check finding no matching
rule. This is correct and expected.

### Retry amplification

From `validate_energy_chain_v2.py`:

```
[GOAL-AGGREGATION]
per attempt: ['46.3612J', '18.6914J']
retry amplification: [0.403]
retry trend: FALLING
```

`retry amplification` = attempt_N / attempt_1. Value 0.403 means the retry
used 40.3% of the failed attempt energy — the retry was cheaper. `FALLING`
trend means retry energy decreases with attempt number (normal recovery).

### Phase breakdown

```
[PHASE-PARTITION]
planning      0.00J   0.0%    0.000s
execution     0.00J   0.0%    0.032s
synthesis     1.57J   2.9%    4.586s
inter_phase  51.97J  97.1%
```

`inter_phase` is orchestration overhead not attributable to a named phase.
For tool-graph tasks with short planning/execution phases, most energy falls
in `inter_phase`. Use multi-step tasks (`tg_sequential_3`, `tg_deep_chain_4`)
for meaningful phase breakdown.

---

## 10. Designing Your Study

### Paper 8: per-type energy cost profiling

**Goal:** Produce Table 2 of Paper 8 — mean wasted energy per failure class.

**Design:**
- 9 separate experiments, one per injectable type
- `tg_sequential_3` task (multiple tool calls, longer execution phase)
- 30 repetitions per type for statistical power
- `vllm_remote/Mistral-7B` or larger model
- `max_injections: 1` per goal to isolate single failure events
- Both linear and agentic workflow modes

**Analysis query:**

```sql
SELECT ga.failure_type, ft.domain, ft.default_retryable,
       COUNT(*) AS n,
       ROUND(AVG(ga.energy_uj)/1e6, 2) AS mean_wasted_j,
       ROUND(STDEV(ga.energy_uj)/1e6, 2) AS std_j
FROM goal_attempt ga
JOIN failure_taxonomy ft ON ga.failure_type = ft.failure_type_id
WHERE ga.outcome = 'failure' AND ga.energy_uj > 0
GROUP BY ga.failure_type
ORDER BY mean_wasted_j DESC;
```

### MLSys (Stephen): recovery depth measurement

**Goal:** Measure how recovery energy varies with injection location (step index).

**Design:**
- One failure type (tool_error)
- Sweep `step_index` from 1 to N (one experiment per step)
- `tg_deep_chain_4` task (4-step tool graph)
- 10 repetitions per step position

**Example scenario for step 2:**

```yaml
scenarios:
  - type: tool_error
    rate: 1.0
    location:
      phase: execution
      step_index: 2
      tool_name: any
    max_injections: 1
```

**Analysis:** Plot `mean_wasted_energy_j` vs `step_index`. Hypothesis:
later-step failures waste more energy (more computation already done).

### Cascade study: compound failures

```yaml
scenarios:
  - type: hallucination        # NOTE: not injectable — remove from real study
    rate: 1.0                  # Use tool_error + timeout instead
    location: {phase: execution, step_index: 2, tool_name: any}
    max_injections: 1

  - type: tool_error
    rate: 1.0
    location: {phase: execution, step_index: 4, tool_name: any}
    max_injections: 1
```

Rules evaluate in order. If rule 1 fires at step 2 and the goal aborts,
rule 2 targeting step 4 logs `status=target_not_reached`. This gives you
cascading failure behavior data.

### Dry run: validate scenario design

Before running a full experiment, validate your scenario rules fire where
you expect:

```yaml
failure_injection:
  enabled: true
  mode: scenario
  scenario_id: "validation_run_v1"
  dry_run: true              # evaluate rules, log decisions, no actual injection
  scenarios:
    - type: tool_error
      rate: 1.0
      location: {phase: execution, step_index: 2, tool_name: database_query}
      max_injections: 1
```

Check `failure_injection_log` after the run:

```sql
SELECT status, target_step, target_phase, target_tool, draw_number
FROM failure_injection_log
WHERE scenario_id = 'validation_run_v1'
ORDER BY injection_id;
```

`status=selected` confirms the rule would have fired. `status=eligible`
with `skip_reason=location_mismatch` tells you the rule never saw the
target step — wrong task or wrong tool name.

---

## 11. Verification and Validation

### Pre-flight checks

Run before every experiment session:

```bash
# Set DB path
DB=$(python3 -c "import sys; sys.path.insert(0,'scripts/tools'); \
  from path_loader import get_alems_db_path; print(get_alems_db_path())")

# Import checks
python3 -c "from core.injection.failure_simulator import simulate_tool_failure, \
  NON_INJECTABLE_TYPES; print('simulator ok', len(NON_INJECTABLE_TYPES), 'non-injectable')"
python3 -c "from core.injection.scenario_loader import build_injector; print('loader ok')"
python3 -c "from core.injection.scenario_injector import ScenarioInjector; print('injector ok')"
python3 -c "from core.execution.failure_classifier import FailureClassifier, \
  VALID_FAILURE_TYPES; print('classifier ok', len(VALID_FAILURE_TYPES), 'types')"

# Schema sync
alems dev sync
```

Expected output:
```
simulator ok 3 non-injectable
loader ok
injector ok
classifier ok 14 types
Sync complete. Schema: v100
```

### Post-experiment validation

```bash
# A1 taxonomy integrity
sqlite3 "$DB" < scripts/validation/validate_a1_taxonomy.sql

# A2 injection log integrity
sqlite3 "$DB" < scripts/validation/validate_a2_injection.sql

# Energy chain validation
python scripts/validate_energy_chain_v2.py --latest
```

### A1 validation — expected output

```
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
```

All `violations` = 0. V5a = 14 (13 canonical + crashed). V8 = 1 (failure_cause
retained by design).

### A2 validation — expected output

```
V1|failure_injection_log columns|17
V2|orphaned injected_type in failure_injection_log|0
V4|non-deterministic seeds|0
V5|injection_log rows missing attempt_id|0
```

### Energy chain validation — what each check means

| Check | Passes when |
|---|---|
| IDLE-SPLIT | pkg energy correctly split into idle + dynamic |
| PROC-ATTR-CPU | CPU fraction attribution computed |
| GOAL-AGGREGATION | Sum of attempt energies matches goal total |
| PHASE-PARTITION | Planning/execution/synthesis energies populated |
| ACTIVITY-DECOMP | LLM compute energy separated from orchestration |
| BOUNDARY | Pre/post task energy populated (requires ETL) |

`GOAL-AGGREGATION` and `IDLE-SPLIT` are the critical checks for injection
experiments. `BOUNDARY` and `ACTIVITY-DECOMP` require ETL to run — they
show `DM` (data missing) until ETL completes.

---

## 12. Known Limitations

### Phase energy zero for short-phase tasks

SPBM samples at 10Hz (100ms intervals). Planning phase for `tg_single_calc`
takes ~0.007ms — below sample resolution. `planning` and `execution` phase
energies show 0J. All energy falls in `inter_phase`.

**Workaround:** Use multi-step tool graph tasks for phase analysis:
- `tg_sequential_2` — 2 tool calls
- `tg_sequential_3` — 3 tool calls
- `tg_deep_chain_4` — 4-step tool chain

### malformed_output with weak models

Weak models (TinyLlama 1B) synthesize past broken tool output and report
`outcome=success` despite injection. This is scientifically valid behavior
but not the intended failure measurement.

**Workaround:** Always use `vllm_remote/Mistral-7B` or stronger model for
injection experiments.

### ETL not auto-running on execute_goal path

`llm_compute_energy_uj`, `prefill_energy_uj`, `decode_energy_uj` are NULL
until the ETL worker processes the queued jobs. The `execute_goal` path
queues ETL jobs but the worker may not drain them immediately.

**Workaround:** Phase energy and RAPL total energy are always correct.
LLM compute breakdown is supplementary. Check `etl_queue` table for
pending jobs and run the worker manually if needed.

### Reasoning-domain types not injectable

`hallucination`, `semantic_error`, `capability_error` cannot be injected.
Attempting to use them in a scenario rule logs `status=suppressed` with
`skip_reason=non_injectable_reasoning_domain_type`.

**Workaround:** Use the detection experiment configs (Section 6).

### network_error energy occasionally zero

On some runs, `network_error` injection shows `energy_uj=0`. This occurs
when the exception path fires before RAPL window attribution. The injection
itself is recorded correctly in `failure_injection_log`.

**Workaround:** Run multiple repetitions and exclude zero-energy rows from
analysis. A fix is tracked as Bug 10.

### winning_attempt_id NULL for failed goals

Goals where all attempts fail have `winning_attempt_id=NULL`. Use LEFT JOIN
when querying through `winning_attempt_id`.

---

## 13. Troubleshooting

### Injection not firing (all outcome=success)

1. Check `failure_injection_log`: if empty, injector was not built.
   Verify `experiment_type: failure_injection` in YAML.

2. Check `max_retries > 0` in retry_policy — required for execute_goal path.

3. Check provider: if using `llama_cpp/tinyllama`, the model may synthesize
   past failures. Switch to `vllm_remote/Mistral-7B`.

4. Clear Python cache: `find . -path "*/__pycache__/*.pyc" -delete`

### failure_type=tool_error for all types

The regex classifier `_detect_error_type` uses `INJECTED[type_id]:` prefix.
If you see `tool_error` for types that should be different, the old
`failure_simulator.py` (pre-v2) may still be running. Verify:

```bash
python3 -c "from core.injection.failure_simulator import _injected_error; \
  print(_injected_error('network_error', 'test'))"
```

Expected: `INJECTED[network_error]: test`
If you see `INJECTED: network failure...` the old version is running.
Copy `failure_simulator_v2.py` to `core/injection/failure_simulator.py`.

### energy_uj=0 on failed attempts

The `execute_goal` path may not be used. Check:

```bash
grep "DEBUG max_retries" /tmp/last_experiment.log
```

If `max_retries=0`, add `max_retries: 3` to retry_policy in your YAML.

### Master summary shows 0.0000 J

Energy is in DB but display query uses wrong path. Check:

```sql
SELECT energy_uj FROM goal_attempt
WHERE created_at > datetime('now','-10 minutes');
```

If rows have non-zero `energy_uj`, the display is reading from `linear_results`
list instead of DB. This is a known issue when `execute_goal` path runs.
The DB values are correct — use the cross-type analysis query for paper results.

### Retry not firing for api_error/network_error

The retry coordinator reads from DB policy which may have `retry_on_api_error=0`.
Override in YAML:

```yaml
retry_policy:
  name: "default"
  max_retries: 3
  retry_on_api_error: true      # explicit YAML override takes precedence over DB
```

---

## 14. Extending the Framework

### Adding a new failure type

1. Insert into `failure_taxonomy`:

```sql
INSERT INTO failure_taxonomy
    (failure_type_id, domain, description, default_retryable,
     default_recovery_strategy, typical_cost_rank)
VALUES
    ('my_new_type', 'execution',
     'Description of what this failure means',
     1, 'immediate_retry', 15);
```

2. Add a case to `core/injection/failure_simulator.py`:

```python
if failure_type_id == "my_new_type":
    logger.info("INJECT my_new_type on %s", tool_name)
    return ToolResult(
        success=False, result=None, tool_name=tool_name, duration_ns=0,
        error=_injected_error("my_new_type", f"my failure description for {tool_name}"),
    )
```

3. Add to `is_retryable` mapping in `core/execution/retry_coordinator.py`:

```python
"my_new_type": policy.retry_on_tool_error,  # map to closest existing flag
```

4. Use immediately in scenario YAML — no migration needed.

### Registering a custom injection engine

Custom injection engines register under `alems.injection_engines` entry point:

```toml
# pyproject.toml
[project.entry-points."alems.injection_engines"]
my_engine = "mypackage.my_engine:MyInjectionEngine"
```

Implement `InjectionEngine` ABC from `core/injection/injection_engine.py`.
The factory in `scenario_loader.py` routes `mode: my_engine` to your class.

### Adding a new scenario to the platform examples

Place scenario YAML files in `config/scenarios/examples/`. Use generic
names that describe the measurement purpose, not the research study:

```
single_type_rate1.yaml          ← deterministic injection, any location
single_type_step_target.yaml    ← injection at specific step
cascade_two_types.yaml          ← compound failure
dry_run_template.yaml           ← scenario design validation
```

Research study YAMLs belong in your experiment workspace outside the
platform — not in `config/experiment_configs/`.
