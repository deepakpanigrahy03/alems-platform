# Console Output and Logging

---
**Status:** PRODUCTION
**Applies to:** every `alems` command, `run_experiment.py`, `test_harness.py`
**Last updated:** 2026-10-05
---

## Overview

A-LEMS writes three kinds of output, each to its own place:

| Output | Where | Purpose |
|---|---|---|
| Results | stdout | summaries, tables, and reports you asked for; machine readable with `--json` |
| Progress and problems | stderr | what the run is doing now, warnings, errors |
| Full record | JSON lines files | every record with its context (host, sandbox, run, stage), for later search |

Keeping results on stdout and everything else on stderr means `alems ... --json | jq` always receives clean JSON.

Nothing is printed or written to disk while energy is being measured. Records raised during a measurement are held in memory and released when the measurement window closes, so observing a run never adds I/O to the energy being measured.

## Modes

| Mode | Console shows | File receives |
|---|---|---|
| `quiet` | errors and final results only | INFO and above |
| `normal` (default) | progress lines, warnings, errors | INFO and above |
| `verbose` | the above plus detail lines per component | DEBUG and above |
| `debug` | everything, including raw dumps | DEBUG and above |

Select a mode with a flag or the environment:

```bash
alems run my_profile --verbose
ALEMS_LOG_MODE=debug alems run my_profile
```

A run in `debug` mode records its effective log level in run provenance; such runs are not eligible for publication tables.

## Reading the Console

Normal mode, one run:

```
measure start  agentic
tokens  in 213  out 270  total 483
measure  agentic  19.69 s  dynamic 1194.5524 J
WARNING  phase_attribution v2: run=141 phase=planning no tick data
```

| Line shape | Meaning |
|---|---|
| no prefix | progress of the run |
| `WARNING`, `ERROR` in column 0 | a problem; errors carry an `ALEMS-DOMAIN-NNNN` code (see the error code reference) |
| `    [component] text` (verbose) | detail from one component, for example `[readers.perf]` or `[energy_engine]` |

`dynamic` is package energy minus idle energy over the measurement window. The final summary table reports attributed energy, the workload share of dynamic energy; the two numbers differ by design.

On an interactive terminal warnings are yellow, errors red, and the final status green or red. Color is never written to pipes, files, or JSON, and is disabled by setting `NO_COLOR`.

## Environment Variables

| Variable | Effect |
|---|---|
| `ALEMS_LOG_MODE` | `quiet`, `normal`, `verbose`, `debug` |
| `ALEMS_LOG_LEVEL` | console threshold by level name, overrides the mode for the console |
| `ALEMS_LOG_COMPONENTS` | per component levels for the file record, for example `readers.perf=DEBUG,energy_engine=DEBUG`; a short module name such as `msr_reader=DEBUG` matches that module anywhere |
| `ALEMS_LOG_DIR` | directory for the host log `alems.jsonl` (default under the data root) |
| `ALEMS_ERROR_DIR` | directory for error records (default under the data root) |
| `NO_COLOR` | disable terminal color |

Precedence, lowest to highest: machine configuration, sandbox overrides, profile, environment, command line.

Older variables are still accepted and mapped: `A_LEMS_DEBUG=1` selects debug mode, `A_LEMS_DEBUG_MODULES=a,b` sets those components to DEBUG in the file record, and `A_LEMS_DEBUG_FILE` is ignored with a notice (use `ALEMS_LOG_DIR`).

## Where Records Are Written

| Record | Location |
|---|---|
| Host log | `<data_root>/<host>/log/alems.jsonl` |
| Per run log | `<store dir>/logs/run_<run_uid>.jsonl` |
| Error records | `<data_root>/<host>/error/<date>/<error_id>.json` |
| Stage lifecycle | table `stage_event` in the sandbox store |

A run refuses to start when no data root is configured (error `ALEMS-CFG-0010`), so records are never silently discarded.

## Verification

```bash
# progress on stderr, nothing on stdout in normal mode
alems run my_profile 2>/dev/null | wc -l
# detail lines appear in verbose
ALEMS_LOG_MODE=verbose alems run my_profile 2>&1 | grep -c "^    \["
# the effective log level of recent runs
sqlite3 "$DB" "SELECT run_id, measurement_log_level FROM runs ORDER BY run_id DESC LIMIT 5;"
```

Replace `my_profile` with a profile from your sandbox and `$DB` with your store path (`alems sandbox info` prints it).

## Known Limitations

- **Module debug is file only:** component levels from `ALEMS_LOG_COMPONENTS` or `A_LEMS_DEBUG_MODULES` reach the file record, not the console. Workaround: read them from the per run log, or use `ALEMS_LOG_MODE=debug`.
- **Lossy log buffer during measurement:** if a measurement produces more log records than the in memory buffer holds, the oldest log records are dropped and counted. Stage events and error records are never dropped; an overflow marks the run invalid instead.
- **Remaining legacy output:** some runner and analysis modules still print directly while their conversion is in progress; those lines appear on stdout and carry no context fields.
