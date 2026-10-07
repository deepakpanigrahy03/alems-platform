# Console Output and Logging

---
**Status:** PRODUCTION
**Applies to:** every `alems` command, `run_experiment.py`, `test_harness.py`
**Last updated:** 2026-10-06
---

## Overview

A-LEMS writes three kinds of output, each to its own place:

| Output | Where | Purpose |
|---|---|---|
| Results | stdout | run header, run report, summaries; machine readable with `--json` |
| Progress and problems | stderr | what the run is doing now, warnings, errors |
| Full record | files beside the store | every record with its context, for later search and diagnosis |

Nothing is printed or written to disk while energy is being measured, except one in place status line on an interactive terminal (see Status Line). Records raised during a measurement are held in memory and released when the measurement window closes, so observing a run never adds I/O to the measured energy.

## Modes

| Mode | Console shows | Files receive |
|---|---|---|
| `quiet` | errors and final results | INFO and above |
| `normal` (default) | progress lines, warnings, errors | INFO and above |
| `verbose` | the above plus detail lines per component | DEBUG and above |
| `debug` | everything, including raw dumps | DEBUG and above |

```bash
alems run my_profile --verbose
ALEMS_LOG_MODE=debug alems run my_profile
```

A run records its effective log level (`runs.measurement_log_level`); a run measured in `debug` mode is never eligible for publication tables.

## Reading the Console

```
measure start  agentic
| measuring agentic  t+12 s  repetition 2/5 [##--------]  20%
tokens  in 213  out 270  total 483
measure  agentic  19.69 s  dynamic 1194.5524 J
WARNING  phase_attribution v2: run=141 phase=planning no tick data
```

| Line shape | Meaning |
|---|---|
| no prefix | progress of the run |
| `WARNING`, `ERROR` in column 0 | a problem; errors carry an `ALEMS-DOMAIN-NNNN` code (see the error code reference) |
| `    [component] text` (verbose, debug) | detail from one component, for example `[readers.perf]` |

`dynamic` is package energy minus idle energy over the measurement window. The run report and the final summary table also show attributed energy, the workload share of dynamic energy.

Color appears only on an interactive terminal; it is never written to pipes, files, or JSON, and `NO_COLOR` disables it.

## Status Line

While a measurement runs, one line is redrawn in place every second: a spinner, the phase, elapsed time, and, when a run has several repetitions, a bar of completed repetitions. During cool down it shows a countdown with a real percentage. The line is erased when the window closes, so it never appears in logs.

| Setting | Effect |
|---|---|
| `ALEMS_HEARTBEAT_S=1` | default interval in seconds |
| `ALEMS_HEARTBEAT_S=0` | off (set this for publication runs) |

It is drawn only on an interactive terminal and never in quiet mode. The interval used is stored per run in `runs.measurement_heartbeat_s` (0 off, NULL before this column existed).

## Run Header and Run Report

Before the first measurement the run header lists what will run and where its records go: engine and git state, sandbox, profile, configuration directory, store, database mode, log and error directories, models with their adapter, tasks, country, baseline, and every reader with its fidelity (`MEASURED` or `LIMITED`).

After the last run, one card per run is read back from the store: path taken (`save_pair`, `save_single`, `execute_goal`), attempt and outcome, energy, LLM calls and tokens, tools, quality, spans, attribution, injection, timing, energy domains, phases, GPU, CPU, thermal, system, network, orchestration, sustainability, stages, validity, error records, the per run log file, and rows written per table. Values that were not measured are omitted, never shown as zero. The comparison table closes the output.

## Where Records Are Written

Logs and error records live beside the store they belong to, so every sandbox, user, and environment keeps its own:

| Record | Location |
|---|---|
| Store log (all records, JSON lines) | `<store dir>/logs/alems.jsonl` |
| Per run log | `<store dir>/logs/run_<run_uid>.jsonl` |
| Error records (one file each, with traceback) | `<store dir>/errors/<date>/<error_id>.json` |
| Stage lifecycle | table `stage_event` in the store |

`<store dir>` is the directory of the store this command uses (see Sandbox Layout and Path Resolution). Commands that run without a store (`help`, `doctor`) fall back to `<data_root>/<host>/log` and `/error`. A run refuses to start when no data root is configured (`ALEMS-CFG-0010`), so records are never discarded silently.

## Debugging a Run

**1. Run in debug mode.** Everything goes to the console and to the files:

```bash
ALEMS_LOG_MODE=debug alems run my_profile 2>&1 | tee /tmp/debug_console.txt
```

**2. Read the per run log.** The run report prints its path (`log file`). Each line is one JSON record with `ts`, `level`, `logger`, `msg`, and the context fields (`run_uid`, `run_id`, `stage_id`, `span_id`, `sandbox_id`):

```bash
LOG=<store dir>/logs/run_<run_uid>.jsonl
jq -r '"\(.ts) \(.level) \(.logger): \(.msg)"' "$LOG" | less          # readable view
jq -c 'select(.level=="WARNING" or .level=="ERROR")' "$LOG"           # problems only
jq -c 'select(.logger | startswith("core.readers"))' "$LOG"           # one component
jq -c 'select(.stage_id=="attribution")' "$LOG"                       # one pipeline stage
```

**3. Follow a failed stage to its error record.** A failed or partial stage stores an error code in `reason` and an `error_ref`:

```bash
sqlite3 "$DB" "SELECT run_id, stage_id, status, reason, error_ref FROM stage_event WHERE error_ref IS NOT NULL ORDER BY run_id DESC LIMIT 10;"
cat <store dir>/errors/*/<error_ref>.json | jq '{code, component, message, traceback}'
```

**4. Turn on debug for one component only.** The file record gets DEBUG for that component; the console stays normal:

```bash
ALEMS_LOG_COMPONENTS=readers.perf=DEBUG,energy_engine=DEBUG alems run my_profile
```

**5. Trace the measurement windows.** `ALEMS_GATE_TRACE` writes one line per window (process id, window start and end, records held, records created). Combined with `strace` it proves that no record was written to disk during measurement:

```bash
ALEMS_GATE_TRACE=/tmp/gate.trace strace -f -ttt \
  -e trace=openat,close,socket,pipe2,eventfd2,write,pwrite64,writev,sendto,sendmsg,connect \
  -o /tmp/run.strace alems run my_profile > /dev/null 2>&1
python3 scripts/tools/strace_window_check.py /tmp/run.strace /tmp/gate.trace
```

A pass shows `observability 0` and `store 0` inside every window.

## Environment Variables

Each setting has one name everywhere: in the shell, in `.sandbox-env`, and in the code.

| Variable | Effect |
|---|---|
| `ALEMS_LOG_MODE` | `quiet`, `normal`, `verbose`, `debug` |
| `ALEMS_LOG_LEVEL` | console threshold by level name |
| `ALEMS_LOG_COMPONENTS` | per component levels for the file record; a short module name such as `msr_reader=DEBUG` matches that module anywhere |
| `ALEMS_LOG_DIR` | explicit log directory (overrides beside the store) |
| `ALEMS_ERROR_DIR` | explicit error record directory |
| `ALEMS_HEARTBEAT_S` | status line interval, 0 off |
| `ALEMS_GATE_TRACE` | file receiving one line per measurement window |
| `NO_COLOR` | disable terminal color |

Precedence, highest first: command line, shell environment, the sandbox file `.sandbox-env`, defaults. Older variables are still accepted: `A_LEMS_DEBUG=1` selects debug mode, `A_LEMS_DEBUG_MODULES=a,b` sets those components to DEBUG, `A_LEMS_DEBUG_FILE` is ignored with a notice.

## Verification

```bash
cd <your sandbox>
alems run my_profile 2>/dev/null | head -3                    # stdout carries the run header only
ALEMS_LOG_MODE=verbose alems run my_profile 2>&1 | grep -c "^    \["
ls "$(dirname "$DB")/logs" | head                             # alems.jsonl and run_*.jsonl beside the store
sqlite3 "$DB" "SELECT run_id, measurement_log_level, measurement_heartbeat_s FROM runs ORDER BY run_id DESC LIMIT 5;"
```

`alems sandbox info` prints the store path to use as `$DB`.

## Known Limitations

- **Component debug is file only:** `ALEMS_LOG_COMPONENTS` reaches the file record, not the console. Workaround: read the per run log, or use `ALEMS_LOG_MODE=debug`.
- **Lossy log buffer during measurement:** if a window produces more log records than the buffer holds, the oldest are dropped and counted. Stage events and error records are never dropped; an overflow marks the run invalid.
- **Log browsing:** logs are read with `jq` as shown; there is no dedicated log browsing command.
- **CLI output:** some administrative commands print plain text and do not yet offer `--json`.
