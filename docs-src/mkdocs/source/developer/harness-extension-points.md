---
**Status:** CURRENT
**Platforms verified:** nvidia_grace
**Last updated:** 2026-10-15
---

# Harness Extension Points

Scheduled profiles ([Scheduled Profiles](../user-guide/scheduled-profiles.md)) call three plugin types. Core loads each by entry point group and the name the profile gives; core never imports a plugin module directly.

| Group | Profile key | Called | Returns |
|---|---|---|---|
| `alems.harness.prepare` | `recovery.prepare` | once per goal, to build the pre attempt hook | a callable `hook(attempt_number) -> dict` |
| `alems.harness.deciders` | `recovery.arms.<arm>` | before every attempt from 2 on, through the hook | a policy string or `None` |
| `alems.harness.validity` | `validity.classifier` | after each goal | `{"codes": [...], "detail": {...}}` |

Registration in the plugin's `pyproject.toml`:

```toml
[project.entry-points."alems.harness.prepare"]
bfcl = "alems_plugin_bfcl.raf_hooks:make_prepare"

[project.entry-points."alems.harness.deciders"]
escalate_stub = "alems_plugin_bfcl.raf_hooks:escalate_stub"

[project.entry-points."alems.harness.validity"]
bfcl_raf = "alems_plugin_bfcl.raf_hooks:classify"
```

Entry points become visible after `pip install -e <plugin>` in the engine's environment.

## Window Rule

Every call on this page runs between attempts, never inside a measurement window [t0, t1]. `execute_goal` invokes the hook immediately before the harness starts an attempt. Work done here (cache resets, HTTP calls, state restore, decisions) is not measured as part of the attempt, and the attempt must not depend on timing set up here.

## Prepare

```python
def make_prepare(executor, base_url, policy, reset_path=None, decide=None):
    ...  # returns hook; core sets executor.pre_attempt_hook = hook
```

`policy` is the planned option (`B0`, `FR_cold`, ..., `RB_warm`). The hook records one dict per attempt on `executor._raf_prepare`; the scheduled runner copies it into the ledger and the validity classifier reads it. A hook never raises for a failed reset or restore; it adds a validity code instead.

The BFCL implementation (`alems_plugin_bfcl.prepare.PreAttempt`) does, per attempt: read the serving engine's cache statistics, reset the prefix cache when the policy requires it, call the decider (attempt 2 on), and restore tool state for FR, SR or RB with a check.

## Decider

```python
def decide(attempt_number: int, executor) -> Optional[str]: ...
```

Called before every attempt from 2 on. The executor carries the failure point, the provider state, the per attempt records and the context, which is everything a failure state needs. A returned policy replaces the current one for this and later attempts; `None` keeps it. Each decision is logged on `executor._raf_decisions` with its latency in ns. `escalate_stub` keeps the policy at attempt 2 and returns `FR_cold` at attempt 3; the R6 controller replaces it.

## Validity Classifier

```python
def classify(conn, goal_id, plan, prepare=None, attempt_meta=None) -> dict: ...
```

`conn` is a read connection. `plan` is the queue goal (task, class, phase, point, ordinal, policy, arm). Empty `codes` means valid; any code triggers the configured rerun. Codes that describe an outcome rather than a defect (for example `RB_NOT_APPLICABLE`) belong in `detail`, not in `codes`.

## Adapter Data Returned to execute_goal

A framework adapter may return, in its result's `execution` block, rows that `execute_goal` writes after the run row exists (real `run_id` and `attempt_id`, outside the window):

| Key | Written to |
|---|---|
| `pending_annotations` | `span_annotations`, linked to the attempt span |
| `pending_state_reuse` | `state_reuse_events` |

The meaning of the written rows is in [Recovery Data](recovery-data.md).
