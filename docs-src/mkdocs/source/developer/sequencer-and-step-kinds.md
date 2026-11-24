# Sequencer Internals and Step Kind Plugins

This page is for developers who change the sequencer or add a new kind of step.
For running controlled experiments, see [Controlled Experiments with the Sequencer](../user-guide/09-controlled-experiments-sequencer.md).

## Architecture

| Component | Location | Responsibility |
|---|---|---|
| Command | `core/cli/cmd_sequence.py` (routed by `scripts/alems`) | arguments, dry run, opening the store, resource lock |
| Plan | `core/sequencer/plan.py` | load, validate, resolve defaults, counterbalanced order, materialise steps |
| Runner | `core/sequencer/runner.py` | settle, gate, attempt lifecycle, failure rules, resume |
| Store | `core/sequencer/store.py` | every sequencer write, each in one short transaction |
| Run link | `core/sequencer/context.py` | attempt id in the step context; mandatory run to attempt link |
| Gate | `core/sequencer/gates.py` | thermal readiness through the platform thermal reader |
| Kinds | `core/sequencer/kinds.py` | built in `idle` and `profile`; discovery of plugin kinds |
| Step kind contract | `alems_sdk.sequencer` | `StepKindABC`, `Availability` |

The sequencer core names no device, engine or tool.
Everything hardware specific is a step kind plugin that reports per request whether it can run here.

## Data model

All tables belong to the `energy_validation` namespace and are created only by its extension migration; they are not in `schema.py`.

| Table | One row per | Key fields |
|---|---|---|
| `energy_validation_seq_sessions` | invocation | resolved plan JSON and hash, source hash, seed, realised order, status |
| `energy_validation_seq_steps` | planned step | cycle, bracket, condition, role, kind, parameters, planned index; materialised before dispatch |
| `energy_validation_seq_attempts` | execution of a step | execution status, reason code and detail, settle and gate record, execution and measurement bounds, baseline id, experiment id |
| `energy_validation_seq_attempt_runs` | run produced by an attempt | attempt id, run id (unique), commit order, repetition, workflow |
| `energy_validation_seq_step_results` | result value of a plugin step | key, value, unit |
| `energy_validation_seq_bracket_eligibility` | bracket and energy domain | eligible, reason, rule version (written by analysis) |

Execution status and comparison eligibility are separate: a step can complete while its bracket is ineligible for one energy domain.

## The durability guarantee

Every committed sequenced run belongs to exactly one durable attempt, also when only part of a paired experiment survives a crash.

How it is achieved:

1. Before a step starts, the runner commits its attempt row with status `running`.
2. The attempt id is placed in a context variable for the duration of the step (`attempt_scope`). Only code running inside the step sees it; a process wide setting is never used. At a subprocess boundary the parent passes `ALEMS_SEQ_ATTEMPT_ID` in that child's environment only.
3. `RunPersistenceService.persist_raw()` stores each run and all its rows in one transaction. Right after the run row, it calls `link_run()`. Outside a step nothing happens. Inside a step the link is mandatory: if the attempt is unknown or not running, or the insert fails, the error rolls the run back.
4. A link failure is also recorded for the attempt, because a caller may log a persistence error and continue; the runner checks it after the step and stops dispatch.
5. Idle steps and plugin steps run as normal measured runs: their work is wrapped in a small executor (`core/sequencer/benchmark.py`) that the existing `run_linear()` measures with every collector and `save_single()` stores with the attempt link. Their experiments have experiment type `benchmark`.
6. Closing an attempt is conditional on it still being `running`, so two resume invocations can never both authorise a retry.

The tests `test_kill_after_first_child_keeps_link` and `test_link_failure_rolls_back_run` exercise the two sides of the commit boundary.

## Store rules

- All writes go through the `DatabaseManager` handle and `db.transaction()`; never open a new connection.
- Any write failure is raised as `SequencerStoreError`; the runner stops dispatch on it, whatever the plan says about treatment failures.
- Foreign keys are checked on the writer connection before dispatch (`check_schema`).

## Writing a step kind plugin

A step kind performs one kind of controlled activity, for example a steady copy at a known size.

### 1. Package layout

```
alems-plugin-mystep/
  pyproject.toml
  alems_plugin_mystep/
    __init__.py
    step.py
```

`pyproject.toml`:

```toml
[project]
name = "alems-plugin-mystep"
version = "1.0.0"
requires-python = ">=3.9"
dependencies = ["alems-sdk>=1.0,<2.0"]

[project.entry-points."alems.sequencer.steps"]
my_step = "alems_plugin_mystep.step:MyStep"

[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"
```

### 2. The step class

```python
from alems_sdk.sequencer import Availability, StepKindABC


class MyStep(StepKindABC):
    """Steady activity of a declared size for a declared duration."""

    KIND = "my_step"
    PARAMS = {
        "size_mib":   {"type": "number", "unit": "MiB", "required": True},
        "duration_s": {"type": "number", "unit": "s",   "required": True},
        "direction":  {"type": "string", "choices": ["h2d", "d2h"], "required": True},
    }

    def is_available(self, step, ctx):
        # Per request: a device may support one direction and not the other.
        if not _device_present():
            return Availability(False, "no_device", "no supported device found")
        return Availability(True)

    def setup(self, step, ctx):
        self._buf = _allocate(step["size_mib"])          # outside the measured interval

    def measure(self, step, ctx):
        done = _run_for(step["duration_s"], self._buf, step["direction"], ctx["cancelled"])
        return {"known_bytes": (done, "B"),
                "achieved_rate": (done / step["duration_s"] / 1e9, "GB/s")}

    def teardown(self, step, ctx):
        _free(self._buf)                                  # outside the measured interval
```

Rules for a step kind:

- Import only `alems_sdk` and the libraries the plugin needs; never `core`.
- Never open the store. Return results from `measure()`; the sequencer stores them with units.
- Keep setup and teardown out of `measure()`; only `measure()` is the measured interval.
- `is_available()` never raises; return a stable reason code and readable detail.
- Read thresholds and paths from the step parameters or the environment layer, never from constants in code.
- Check `ctx["cancelled"]()` in long loops.

Optional hooks: `prepare(step, ctx)` runs before the bracket's pre idle (for a probe that must not warm the bracket; its return value reaches `ctx["prepared"]`), and `evaluate(step, ctx, results, run)` runs after the run is stored with the run's in memory samples, for checks such as steadiness. `measure()` raises `StepFailed(code, detail)` when the work did not complete; the run is still stored and the step is marked failed with that reason.

### 3. Install and check

```bash
pip install -e alems-plugin-mystep
alems plugins                                      # the plugin is listed
alems sequence plans/my_step_plan.yaml --dry-run   # parameters validated, order printed
```

`alems dev install-plugins` installs every `alems-plugin-*` folder, so a plugin placed in the repository is installed with the platform.

### 4. Test

Test the class without a device by checking `validate()` and an unavailable `is_available()`, as `test_plugin_kind_validation_and_skip` in `tests/test_sequencer.py` does.

## Configuration layers

| Value | Plan key | `~/.alemsrc` | Default |
|---|---|---|---|
| Idle duration | `sequence.baseline.duration_s` | `ALEMS_SEQ_BASELINE_S` | 60 |
| Settle time | `sequence.settle_s` | `ALEMS_SEQ_SETTLE_S` | 30 |
| Cycles | `sequence.cycles` | `ALEMS_SEQ_CYCLES` | 1 |
| Idle samples | `baseline.num_samples` | `ALEMS_SEQ_IDLE_SAMPLES` | 10 |
| Gate threshold | `sequence.gate.max_temp_c` | `ALEMS_SEQ_GATE_MAX_TEMP_C` | gate off |
| Gate sensors | `sequence.gate.sensor_pattern` | `ALEMS_SEQ_GATE_SENSOR_PATTERN` | all |
| Gate maximum wait | `sequence.gate.max_wait_s` | `ALEMS_SEQ_GATE_MAX_WAIT_S` | 600 |
| Gate poll period | `sequence.gate.poll_s` | `ALEMS_SEQ_GATE_POLL_S` | 5 |

The plan wins over `~/.alemsrc`, and the shell environment wins over `~/.alemsrc`.

## Testing

`tests/test_sequencer.py` runs against an in memory store with the real extension migration: plan validation and reproducible order, counterbalancing, the kill and rollback tests, conditional finish, pre idle failure, link failure stopping dispatch, the gate, and plugin validation.
Run it with `python -m pytest -q tests/test_sequencer.py`.
