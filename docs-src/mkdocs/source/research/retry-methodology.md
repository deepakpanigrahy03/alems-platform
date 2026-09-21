# Retry and Tool Failure Methodology

## Scope

This document covers retry policy management, failure classification, and
deterministic failure injection. For energy attribution of failures see
`20-tool-failure-methodology.md`. For tool execution instrumentation see
`24-tool-instrumentation-methodology.md`.

---

## Motivation — Why Retry Energy Matters

Production LLM API systems fail 15-30% of calls due to rate limits, timeouts,
context window overflow, and tool errors. A-LEMS captures the full energy cost
of recovery — including every failed attempt before a successful result.

An agentic system making 3-5 tool calls per task has 3-5x the failure surface
of a linear system. The energy overhead of these failures is the core signal
in paper Figure 3 (wasted energy taxonomy). Without retry tracking, this
energy is invisible.

---

## Retry Policy
*method_id: `retry_policy_v1` | confidence: 0.90*

### Four Canonical Policies

| Policy | max_retries | wrong_answer retry | backoff |
|---|---|---|---|
| `no_retry` | 0 | No | 0s |
| `default` | 1 | No | 0s |
| `aggressive` | 3 | Yes | 2s |
| `conservative` | 1 | No | 5s |

Stored in `retry_policy` table. Per-category overrides in `task_retry_override`
replace `max_retries` only — failure-type flags remain from template policy.

### Policy Resolution Order

1. Load template policy by name from experiment config `retry_policy.name`
2. Check `task_retry_override` for task's category
3. If override exists, replace `max_retries` only

### Energy Accounting

Wasted energy from failed attempts is captured via `goal_attempt.energy_uj`
snapshots at `finish_attempt()` time. ETL rolls these into:
- `goal_execution.overhead_energy_uj = total_energy_uj - successful_energy_uj`
- `overhead_fraction = 1.0` for fully failed goals
- `0 < overhead_fraction < 1.0` for goals that succeeded after retries

### Confidence Rationale

0.90 — policy logic is deterministic. 0.10 uncertainty reflects that
`context_overflow` is never retried regardless of policy, assuming prompt
will not change between attempts (structural failure).

---

## Failure Classification
*method_id: `failure_classification_v1` | confidence: 0.85*

### Canonical Failure Types

| Type | Source |
|---|---|
| `timeout` | TimeoutError, concurrent.futures.TimeoutError, httpx.TimeoutException |
| `api_error` | ConnectionError, ConnectError, APIError |
| `rate_limit` | RateLimitError, HTTP 429, "Too Many Requests" |
| `context_overflow` | ContextLengthExceeded, "exceed context window", "context_length" |
| `tool_error` | run_result.tool_error = True |
| `wrong_answer` | quality_score < 0.5 with no exception |
| `crashed` | Any unrecognised exception |

### Four-Layer Classification Priority

Layer 1: Exception type check — infrastructure failures raised by harness.
Layer 2: `execution.error_type` — set by agentic structured detection (most reliable).
Layer 3: `execution.error_message` — provider errors caught internally by harness.
Layer 4: Scan `step_results[*].result` for "Error:" prefix strings.
Fallback: `execution.status == "failure"` → `api_error`.

**Critical:** Provider errors (HTTP 429, context overflow) are often caught
inside the harness and returned as result dicts with `error_message` set,
not raised as exceptions. All four layers must be checked in order.

**Conservative default:** `exec_dict.get("status", "failure")` — absent status
is treated as failure, not success. Prevents malformed results from silently
passing as successful runs.

### Structured Failure Detection

Agentic executor scans step results after the full execution loop:

```python
step_errors = [
    sr.get("result", "")
    for sr in step_results
    if isinstance(sr.get("result", ""), str)
    and sr.get("result", "").startswith("Error:")
]

if failed_steps == 0:
    execution_status = "success"
elif failed_steps < total_steps:
    execution_status = "partial_failure"   # some steps failed, synthesis continued
else:
    execution_status = "failure"           # all steps failed
```

`partial_failure` is treated as `failure` for goal tracking. Paper can filter
by `execution.failed_steps > 0` for finer analysis.

### Retryable vs Non-Retryable

| Type | Retryable | Rationale |
|---|---|---|
| `rate_limit` | Yes | Transient — backoff and retry |
| `timeout` | Yes | Transient — may succeed on retry |
| `api_error` | Yes | Transient network issue |
| `context_overflow` | No | Structural — same prompt fails again |
| `tool_error` | Yes | External tool may recover |
| `wrong_answer` | Yes (if policy allows) | Model may produce different answer |
| `crashed` | No | Unknown cause — unsafe to retry |

### Confidence Rationale

0.85 — exception name matching uses string checks to avoid hard imports of
provider SDKs. New provider exception names not matching known patterns fall
through to `crashed` — safe but loses classification specificity.

---

## Two Execution Paths

These paths must never be merged:

**Normal path** (`save_pair()`/`save_single()`):
`max_retries = 0`. Harness runs once, result saved directly.

**Retry path** (`execute_goal()` → `RunPersistenceService`):
`max_retries > 0`. `execute_goal()` owns full lifecycle. Harness is NOT
called from rep loop — `execute_goal()` calls it internally per attempt.
One `runs` row inserted after all attempts complete.

---

## Failure Injection v2
*method_id: `failure_injection_v2` | confidence: 1.0*
*Supersedes: `failure_injection_v1`*

### What Changed in v2

| Aspect | v1 | v2 |
|---|---|---|
| Seeding | `hash(tool, run_id, attempt) & 0xFFFFFFFF` — unstable across processes | `SHA-256(scenario_id:rep:attempt:kind:tool)` — stable everywhere |
| Modes | Single mode (probabilistic) | Three modes: deterministic_validation, deterministic_stress, statistical |
| Cross-provider | No shared schedule — different exp_ids → different injection | `scenario_id` enables shared schedule across providers |
| Clustering | Random — may cluster at start | Evenly-spaced slots — uniform distribution |
| Audit trail | None | Full per-decision log with seed values |
| Failure budget | Global count | Per-kind (timeout separate from tool_failure) |

### Stable SHA-256 Seeding

```
seed_input = f"{scenario_id}:{rep_num}:{attempt_num}:{kind}:{tool_name}"
digest     = SHA-256(seed_input)
rand       = first_8_bytes_as_uint64 / 2^64   → float in [0, 1)
```

Python `hash()` is PYTHONHASHSEED-randomised since Python 3.3 — different
every process. SHA-256 produces identical output across platforms, Python
versions, and process restarts. Reviewers can verify any injection decision
from the published audit log.

### Three Modes

**deterministic_validation** — Exact N failures at evenly-spaced draw positions.
Rates ignored. Same schedule every run. For CI and architecture validation.

**deterministic_stress** — Every attempt fails. For retry exhaustion testing.
Respects `max_retries` — never produces infinite loops.

**statistical** — SHA-256-seeded Bernoulli draws against configured rates.
For paper Figure 3 data collection. Requires 30+ repetitions.

### Evenly-Spaced Slot Algorithm

For N total draws and K required failures:

$$slot_i = \text{round}\left(\frac{(i + 0.5) \times N}{K}\right), \quad i = 0, 1, \ldots, K-1$$

Example: N=6, K=2 → slots {2, 4} not {1, 2}.

Avoids pathological clustering at experiment start which would conflate
injection warmup effects with real measurement signal.

### scenario_id — Cross-Provider Fairness

Without `scenario_id`: different providers get different `exp_id` → different
SHA-256 inputs → different injection slots. Comparison is invalid.

With `scenario_id = "failure_study_v1"`: all providers share identical slots.
Energy difference = provider efficiency, not scheduling artifact.

Rule: always set `scenario_id` for multi-provider studies. Bump version when
injection config changes.

### Post-Harness Injection Only

Every LLM execution consumes real RAPL energy regardless of logical outcome.
Injection modifies the result dict AFTER harness completes:

```
harness runs → RAPL captures energy
→ post-harness injection: result["execution"]["status"] = "failure"
→ goal_execution_manager reads status → outcome = "failure"
→ tool_failure_events row created with real energy data
→ retry_coordinator decides retry → attempt 2 → harness runs again
```

Pre-harness injection produces zero-energy records — invalid for paper claims.

### Workload-Aware Injection

| Workload | Valid Injection |
|---|---|
| Pure LLM tasks | timeout, rate_limit, api_error, context_overflow |
| Tool graph tasks | tool_error, timeout |

Tool failure injection on pure LLM tasks is invalid — `_dispatch_tool` is
never called so injection never fires.

### Validating the Injector

```python
from core.execution.failure_injector import FailureInjector, _evenly_spaced_slots

# Verify slot spacing
assert sorted(_evenly_spaced_slots(6, 2)) == [2, 4]

# Verify deterministic mode produces exact N failures
fi = FailureInjector(
    {'enabled': True, 'mode': 'deterministic_validation',
     'min_failures': 2, 'total_draws_estimate': 6},
    'failure_injection'
)
fi.set_exp_id(999, total_draws=6)
results = [fi.maybe_inject_timeout(r, a) for r in range(1, 4) for a in range(1, 3)]
summary = fi.injection_summary()
assert summary['by_kind']['timeout']['planned_failures'] == \
       summary['by_kind']['timeout']['realized_failures'], \
    "Injection schedule not met — increase repetitions or fix total_draws_estimate"
```

### Audit Trail

```python
fi.get_audit_log()
# Returns list of dicts:
# exp_id, scenario_id, rep_num, attempt_num, kind,
# tool_name, injected, seed_value, mode, draw_index
```

Publish as supplementary material — reviewers can verify the exact injection
schedule and reproduce any experiment.

### Confidence Rationale

1.0 — injection logic is pure deterministic arithmetic for deterministic modes,
SHA-256 for statistical mode. No measurement uncertainty in either case.

---

## Planned Experiments

### Retry Cost Curve
Vary `max_retries` = 0, 1, 2, 3 with `retry_study` experiment_type.
Measure: success %, joules/goal, overhead_fraction.
Expected: diminishing returns beyond max_retries=2 for most failure types.

### Failure Type Sensitivity
Inject timeout vs rate_limit vs api_error independently (separate YAMLs).
Compare energy overhead per failure type.
Expected: rate_limit most expensive (backoff wait), timeout cheapest (fails fast).

### Local vs Cloud Failure Comparison
groq (cloud): organic 429 rate limits.
llama_cpp (local): organic context overflow at 512-token TinyLlama limit.
Expected: different failure type distributions, comparable overhead_fraction.
Use `scenario_id` to ensure identical injection schedules across providers.
---
**Method ID:** ear_policy_v1
**Schema version:** v105 (ear_policy, ear_policy_rules, ear_decision_log), v90002 (failure_cost_profile)
**Platforms verified:** NVIDIA Grace GB10 (aarch64)
**Status:** PRODUCTION
**Last updated:** 2026-09-20
---

---

## Energy-Aware Retry (EAR) { #ear-policy-v1 }

### Overview

Standard retry policies repeat failed attempts unconditionally up to a fixed
maximum count. This is energy-wasteful: a failure type with a 5% recovery rate
spends 20x the single-attempt energy budget on average to reach one success.

Energy-Aware Retry (EAR) conditions each retry decision on three calibrated
signals derived from historical experiment data:

- **Recovery cost** (`recovery_cost_uj_mean`) — how much energy a recovery
  attempt for this failure type typically consumes
- **Recovery success rate** (`recovery_success_rate`) — fraction of failures
  of this type where the next attempt succeeded
- **Cost per successful recovery** (`cost_per_recovery_success`) — expected
  energy investment to obtain one successful recovery

EAR aborts retry when the expected cost exceeds the available budget or when
the calibrated success probability falls below a researcher-defined threshold.
The flat retry policy (always retry up to max) remains the default and is
unchanged for all existing experiments.

---

### Platform Coverage

| Platform | Architecture | Measurement Source | Status |
|---|---|---|---|
| NVIDIA Grace GB10 | aarch64 | SPBM + goal_attempt.energy_uj | VERIFIED |
| Intel x86 | x86_64 | RAPL + goal_attempt.energy_uj | PENDING |
| AMD Ryzen | x86_64 | RAPL + goal_attempt.energy_uj | PENDING |
| Apple M1 Pro | arm64 | IOKit + goal_attempt.energy_uj | PLANNED |

---

### Schema

#### Core tables (schema v105, present on all machines)

**ear_policy** — one named EAR policy per row

| Column | Type | Description |
|---|---|---|
| ear_policy_id | INTEGER | Primary key |
| policy_name | TEXT | Unique name, referenced from YAML |
| description | TEXT | Human description |
| calibration_scope | TEXT | `experiment_group` (EAR v1) |
| created_at | TIMESTAMP | Creation time |

**ear_policy_rules** — one row per (policy, failure_type) pair

| Column | Type | Description |
|---|---|---|
| rule_id | INTEGER | Primary key |
| ear_policy_id | INTEGER | FK to ear_policy |
| failure_type_id | TEXT | Failure type from failure_taxonomy |
| max_attempts | INTEGER | Maximum retry attempts for this type |
| cost_threshold_uj | REAL | Budget must exceed this to allow retry (µJ) |
| calibrated_success_prob | REAL | Success probability from A3 profiling |
| success_probability_threshold | REAL | Minimum probability to allow retry |
| action | TEXT | `retry`, `abort`, or `fallback` |
| priority | INTEGER | Higher = evaluated first |

**ear_decision_log** — one row per runtime retry decision

| Column | Type | Description |
|---|---|---|
| decision_id | INTEGER | Primary key |
| run_id | INTEGER | FK to runs |
| attempt_id | INTEGER | FK to goal_attempt |
| failure_type_id | TEXT | Failure type that triggered decision |
| attempt_number | INTEGER | Attempt number (1-indexed) |
| budget_remaining_uj | REAL | Energy budget at decision time (µJ) |
| action | TEXT | Decision: `retry`, `abort`, `fallback` |
| reason | TEXT | Reason code (see Reason Codes below) |
| calibration_cost_uj | REAL | Cost threshold used at decision time |
| calibration_success_prob | REAL | Success probability used at decision time |
| decided_at | TIMESTAMP | Decision timestamp |

#### Extension table (schema v90002, present on all machines via core track)

**failure_cost_profile** — aggregated cost statistics per (failure_type, experiment_group)

| Column | Type | Description |
|---|---|---|
| profile_id | INTEGER | Primary key |
| failure_type_id | TEXT | Failure type from failure_taxonomy |
| experiment_group | TEXT | experiments.group_id |
| sample_count | INTEGER | Number of failure events |
| recovery_cost_uj_mean | REAL | Mean recovery energy (µJ) |
| recovery_cost_uj_median | REAL | Median recovery energy (µJ) |
| recovery_cost_uj_p25 | REAL | 25th percentile |
| recovery_cost_uj_p75 | REAL | 75th percentile |
| recovery_cost_uj_std | REAL | Standard deviation |
| recovery_success_rate | REAL | Fraction where next attempt succeeded |
| recovery_success_count | INTEGER | Raw count of successful recoveries |
| cost_per_recovery_success | REAL | E[cost] / P[success] (µJ) |
| computed_at | TIMESTAMP | ETL run time |

---

### Method Provenance

**failure_cost_profile ETL**
- method_id: `failure_cost_profile_etl_v1`
- provenance: CALCULATED
- layer: orchestration
- confidence: 0.90
- formula: `cost_per_recovery_success = E[recovery_cost_uj] / P[recovery_success]`
- confidence justification: 0.90 because EAR v1 uses experiment-group-level
  aggregation. Recovery costs vary by task category and model — the aggregate
  masks this variance. Conditional calibration (per-model, per-task) would
  reach 1.0 but is deferred to Paper 8 future work.

**EAR policy engine**
- method_id: `ear_policy_v1`
- provenance: CALCULATED
- layer: orchestration
- confidence: 0.90 (same calibration scope limitation as above)

---

### Designing an EAR Experiment

EAR requires three sequential stages. Each stage depends on data from the previous.

#### Stage 1: Failure profiling (A3)

Run a failure injection experiment to collect recovery cost data.
The experiment must use `experiment_type: failure_injection` and enough
repetitions to produce statistically meaningful profiles (minimum 10 per failure type,
recommended 20+).

```yaml
# profiling_experiment.yaml
study:
  name: "Failure profiling — tool_error baseline"
  experiment_type: "failure_injection"
  experiment_goal: "Collect recovery cost data for EAR calibration"
  workflow_modes: ["agentic"]

tasks:
  - id: tg_single_db          # must have tool_calls >= 1

providers:
  - name: vllm_remote
    model_id: Mistral-7B-Instruct-v0.3

execution:
  repetitions: 20
  cool_down_seconds: 5
  save_db: true

retry_policy:
  name: "default"
  max_retries: 3
  retry_on_tool_error: true
  backoff_seconds: 1.0

failure_injection:
  enabled: true
  mode: scenario
  scenario_id: "profiling_v1"
  scenarios:
    - type: tool_error
      rate: 1.0           # inject on every tool call
      location:
        phase: execution
        step_index: any
        tool_name: any
      # No max_injections — unlimited per rule (default)
```

**Critical YAML options for failure_injection:**

| Key | Type | Description |
|---|---|---|
| `enabled` | bool | Must be `true` to activate injection |
| `mode` | string | `scenario` (recommended) or `statistical` |
| `scenario_id` | string | Provenance label stored in every log row |
| `scenarios[].type` | string | Failure type from failure_taxonomy |
| `scenarios[].rate` | float | Bernoulli draw probability (0.0–1.0) |
| `scenarios[].location.phase` | string | `planning`, `execution`, `synthesis`, `any` |
| `scenarios[].location.step_index` | int or `any` | Target step in trajectory |
| `scenarios[].location.tool_name` | string or `any` | Target tool name |
| `max_injections` | int | Global cap across all reps (omit for unlimited) |

**Injection status codes** (in `failure_injection_log.status`):

| Status | Meaning |
|---|---|
| `eligible` | Rule matched this tool call — draw was made |
| `injected` | Draw passed rate threshold — failure fired |
| `skipped` | Draw did not pass rate threshold — normal execution |
| `suppressed` | Draw passed but `max_injections` global cap was reached |
| `target_not_reached` | Targeted step/phase/tool was never reached in trajectory |

**Common mistake:** setting `max_injections: 1` with `repetitions: 10` produces
only 1 injection total across all 10 reps. The cap is global per rule, not per goal.
Omit `max_injections` for profiling experiments.

**Task selection:** injection only fires when the task has tool dispatch
(`tool_calls >= 1` in tasks.yaml). Tasks with `tool_calls: 0` produce
`status=eligible` but injection never reaches `_dispatch_tool`. Use `tg_single_db`,
`tg_sequential_2`, or any task with real tool calls.

Run the experiment, then get the group_id:

```bash
sqlite3 $DB "SELECT group_id FROM experiments ORDER BY exp_id DESC LIMIT 1;"
```

#### Stage 2: Cost profiling ETL (A3)

```bash
python scripts/etl/failure_cost_profile_etl.py \
  --group <group_id> \
  --verbose
```

Verify profiles were written:

```bash
sqlite3 $DB "
SELECT failure_type_id,
       sample_count,
       ROUND(recovery_cost_uj_mean/1e6, 4) AS cost_j_mean,
       ROUND(recovery_success_rate, 4)     AS success_rate,
       ROUND(cost_per_recovery_success/1e6, 4) AS cost_per_success_j
FROM failure_cost_profile
WHERE experiment_group = '<group_id>'
ORDER BY cost_per_recovery_success DESC;"
```

Minimum viable profile: `sample_count >= 10` per failure type.
Profiles with `sample_count < 5` should not be used for calibration.

#### Stage 3: EAR calibration (A4)

```bash
python core/retry/ear_calibrator.py \
  --group <group_id> \
  --policy ear_v1 \
  --verbose
```

Optional calibration parameters:

| Flag | Default | Description |
|---|---|---|
| `--success-threshold` | 0.10 | Minimum success probability to allow retry |
| `--budget-multiplier` | 2.0 | `cost_threshold = mean_cost * multiplier` |
| `--dry-run` | false | Log rules without writing to DB |

Verify rules were written:

```bash
sqlite3 $DB "
SELECT epr.failure_type_id,
       epr.action,
       epr.max_attempts,
       ROUND(epr.cost_threshold_uj/1e6, 4) AS cost_threshold_j,
       ROUND(epr.calibrated_success_prob, 4) AS success_prob
FROM ear_policy_rules epr
JOIN ear_policy ep ON epr.ear_policy_id = ep.ear_policy_id
WHERE ep.policy_name = 'ear_v1'
ORDER BY epr.failure_type_id;"
```

#### Stage 4: Run EAR experiment

Create a new experiment YAML with `engine: ear`:

```yaml
# ear_experiment.yaml
study:
  name: "EAR policy evaluation"
  experiment_type: "failure_injection"
  experiment_goal: "Compare EAR vs flat retry energy efficiency"
  workflow_modes: ["agentic"]

tasks:
  - id: tg_single_db

providers:
  - name: vllm_remote
    model_id: Mistral-7B-Instruct-v0.3

execution:
  repetitions: 20
  cool_down_seconds: 5
  save_db: true

retry_policy:
  engine: ear                     # activates EARAdapter
  ear_policy_name: ear_v1         # must match ear_policy.policy_name in DB
  max_retries: 3
  retry_on_tool_error: true
  backoff_seconds: 1.0
  # budget_uj: 50000000           # optional per-goal energy budget (50 J)

failure_injection:
  enabled: true
  mode: scenario
  scenario_id: "ear_eval_v1"
  scenarios:
    - type: tool_error
      rate: 1.0
      location:
        phase: execution
        step_index: any
        tool_name: any
```

**retry_policy YAML keys for EAR:**

| Key | Type | Description |
|---|---|---|
| `engine` | string | `flat` (default) or `ear` |
| `ear_policy_name` | string | Policy name in `ear_policy` table |
| `budget_uj` | float | Optional per-goal energy budget in µJ |
| `max_retries` | int | Maximum retries (passed to flat policy fallback) |
| `name` | string | Flat policy name (used when `engine: flat`) |

---

### Reason Codes

Every `ear_decision_log` row carries a `reason` field explaining the decision:

| Reason | Meaning |
|---|---|
| `ear_allows` | All checks passed — retry permitted |
| `no_ear_rule_for_type` | No rule exists for this failure type — abort |
| `ear_max_attempts_exceeded` | `attempt_number >= rule.max_attempts` — abort |
| `ear_budget_exhausted` | `budget_remaining < cost_threshold_uj` — abort |
| `ear_success_prob_too_low` | `calibrated_success_prob < threshold` — abort |
| `flat_policy_allows` | FlatRetryAdapter: policy permits retry |
| `flat_policy_not_retryable` | FlatRetryAdapter: failure type not retryable |
| `flat_max_retries_exceeded` | FlatRetryAdapter: attempt count exceeded |

---

### Query Reference

**Q1: What did EAR decide for each failure type in an experiment?**

Applies to: all platforms. Replace `<run_id_min>` with the first run_id of your experiment.

```sql
SELECT edl.failure_type_id,
       edl.action,
       edl.reason,
       COUNT(*)                                         AS decisions,
       ROUND(AVG(edl.calibration_success_prob), 3)     AS avg_calib_prob,
       ROUND(AVG(edl.calibration_cost_uj)/1e6, 4)      AS avg_cost_threshold_j
FROM ear_decision_log edl
WHERE edl.run_id >= <run_id_min>
GROUP BY edl.failure_type_id, edl.action, edl.reason
ORDER BY edl.failure_type_id, decisions DESC;
```

Expected output: one row per (failure_type, action, reason) combination.
Typical values: `abort|ear_max_attempts_exceeded` when attempt count is exhausted.

**Q2: Cost profile ranking — which failure types are most expensive to recover from?**

```sql
SELECT failure_type_id,
       experiment_group,
       sample_count,
       ROUND(recovery_cost_uj_mean/1e6, 4)      AS cost_j_mean,
       ROUND(recovery_success_rate, 3)           AS success_rate,
       ROUND(cost_per_recovery_success/1e6, 4)  AS cost_per_success_j,
       DENSE_RANK() OVER (
           PARTITION BY experiment_group
           ORDER BY cost_per_recovery_success DESC
       )                                         AS cost_rank
FROM failure_cost_profile
WHERE experiment_group = '<group_id>'
ORDER BY cost_rank;
```

Expected output: one row per failure type, ranked by cost efficiency.
Rank 1 = most expensive recovery type.

**Q3: EAR vs flat energy comparison across experiment groups**

```sql
SELECT r.workflow_type,
       e.group_id,
       COUNT(DISTINCT ga.goal_id)               AS goals,
       ROUND(SUM(ga.energy_uj)/1e6, 4)          AS total_energy_j,
       ROUND(AVG(ga.energy_uj)/1e6, 4)          AS avg_energy_per_attempt_j,
       SUM(CASE WHEN ga.outcome='success' THEN 1 ELSE 0 END) AS successes
FROM goal_attempt ga
JOIN runs r     ON ga.run_id  = r.run_id
JOIN experiments e ON r.exp_id = e.exp_id
WHERE e.group_id IN ('<flat_group_id>', '<ear_group_id>')
GROUP BY r.workflow_type, e.group_id
ORDER BY e.group_id, r.workflow_type;
```

Expected output: two rows per workflow type — one for flat, one for EAR.
Compare `total_energy_j` and `successes` to quantify EAR benefit.

**Q4: Injection audit — verify injection fired as expected**

```sql
SELECT fil.status,
       fil.injected_type,
       COUNT(*)                                  AS count
FROM failure_injection_log fil
JOIN goal_attempt ga ON fil.attempt_id = ga.attempt_id
JOIN runs r          ON ga.run_id = r.run_id
JOIN experiments e   ON r.exp_id = e.exp_id
WHERE e.group_id = '<group_id>'
GROUP BY fil.status, fil.injected_type
ORDER BY fil.status;
```

Expected output: `injected|tool_error|N` where N matches repetitions.
If `suppressed` count is high, check `max_injections` setting in YAML.
If `eligible` count is high but `injected` is 0, check that `rate > 0`.

---

### Verification

Run after completing Stages 1 through 4 to confirm the full pipeline is working:

```bash
# 1. Profiles exist
sqlite3 $DB "SELECT failure_type_id, sample_count FROM failure_cost_profile \
  WHERE experiment_group = '<group_id>';"
# Expected: one row per injected failure type with sample_count > 0

# 2. Rules calibrated
sqlite3 $DB "SELECT failure_type_id, action, max_attempts \
  FROM ear_policy_rules epr \
  JOIN ear_policy ep ON epr.ear_policy_id = ep.ear_policy_id \
  WHERE ep.policy_name = 'ear_v1';"
# Expected: one row per failure type in profiles

# 3. EAR decisions logged
sqlite3 $DB "SELECT action, reason, COUNT(*) FROM ear_decision_log \
  GROUP BY action, reason;"
# Expected: rows with action in (retry, abort, fallback)

# 4. Validation suite
sqlite3 $DB < scripts/validation/validate_a3_cost_profiling.sql
sqlite3 $DB < scripts/validation/validate_a4_ear.sql
# Expected: V1-V8 pass with 0 rows on error checks
```

---

### Known Limitations

- **Aggregate calibration scope:** EAR v1 calibrates at experiment-group level,
  aggregating across all task categories and models in the group. If `tool_error`
  recovery costs 50 J on math tasks and 200 J on coding tasks, EAR v1 uses the
  aggregate (e.g. 125 J). This is a finding about EAR v1 design, not a defect.
  Conditional calibration (per-model, per-task, per-provider) is stated as future
  work in Paper 8.

- **Minimum sample requirement:** profiles with `sample_count < 10` produce
  unreliable percentile estimates (p25/p75/std). Do not use for calibration.
  Run at least 10 repetitions per failure type before profiling.

- **Hallucination profiles require:** `hallucination_events` is populated
  by the quality judge pipeline (`quality.enabled: true` in experiment YAML).
  Until at least one quality-enabled experiment runs, hallucination type is
  excluded from cost profiles. The ETL logs a warning when this occurs.

- **Budget tracking:** `budget_remaining_uj` is `NULL` in the current
  implementation (per-goal budget tracking not yet wired). The
  `ear_budget_exhausted` reason code will not fire until budget tracking
  is implemented. All other reason codes are active.

- **Non-injectable failure types:** `hallucination`, `semantic_error`,
  `capability_error` cannot be injected via `ScenarioInjector`. These types
  are detected by the quality judge path. Cost profiles for these types
  require detection experiments, not injection experiments.
