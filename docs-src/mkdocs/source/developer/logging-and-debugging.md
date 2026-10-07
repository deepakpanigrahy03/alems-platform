# Logging and Debugging for Developers

---
**Status:** PRODUCTION
**Audience:** anyone changing A-LEMS code
**Last updated:** 2026-10-06
---

## Overview

A-LEMS measures energy, so its own output must never add energy to a measurement. Every line a component emits follows four rules:

1. **Never `print`.** Operational output goes to `logging`; results a user asked for go to the console renderer. A test fails the build on any `print` with emoji or a DEBUG tag in core.
2. **Defer formatting.** Pass values as arguments, never as an f string: `logger.debug("samples %d", n)`, not `logger.debug(f"samples {n}")`. Inside a measurement window the record is held in memory and formatted only after the window closes.
3. **Pick the right tier.** Progress, detail, or dump (below).
4. **Use `%s` for ids that may not exist yet.** `run_id`, `attempt_id`, and `goal_id` are `None` until their rows are written.

## The Four Modes

| Mode | Console | Files | Use it for |
|---|---|---|---|
| `quiet` | errors, final results | INFO and above | scripted batch runs |
| `normal` | progress, warnings, errors | INFO and above | everyday runs |
| `verbose` | plus component detail lines | DEBUG and above | checking what each component did |
| `debug` | everything, including raw dumps | DEBUG and above | finding a bug |

`ALEMS_LOG_MODE=<mode>` or `--quiet`, `--verbose`. Debug runs are recorded as such and are never eligible for publication tables.

## The Three Tiers

| Tier | Call | Visible in | Content |
|---|---|---|---|
| Progress | `logging.getLogger("alems.progress").info(...)` | normal and above | one line per milestone: measure start and end, tokens, repetition, persisted |
| Detail | `logger.info(...)` (module logger) | verbose and above, always in files | values that explain a run: sample counts, latencies, reader choices, durations |
| Dump | `logger.debug(...)` | debug, files in verbose | raw structures: dicts, key lists, object ids |

Warnings and errors use `logger.warning` and `logger.error`; errors that end a stage carry an `ALEMS-DOMAIN-NNNN` code through the error catalog.

User results (tables, summaries, reports) use the renderer:

```python
from core.observability.console import get_console
con = get_console()
con.section("summary")
con.kv("energy", "%.4f J" % joules)
con.line(con.style("done", "green"))
```

The renderer writes to stdout, honours `--json` and `--quiet`, and colors only on a terminal. Never call it inside a measurement window.

## Writing a Log Line

```python
import logging
logger = logging.getLogger(__name__)          # module logger: name becomes the component

logger.info("energy samples collected: %d", len(samples))     # detail
logger.debug("first sample: %s", samples[0])                  # dump
logger.warning("thermal zone %s missing; skipped", zone)      # problem
logger.info("finish_attempt: run_id=%s", run_id)              # %s: may be None
```

Do not:

```python
print("Loaded config")                              # print: breaks --json, writes inside windows
logger.debug(f"state {big_dict}")                   # f string: formats even when debug is off
logger.info("run_id=%d", run_id)                    # %d with a possible None
logger.debug("keys %s", dir(obj))                   # expensive dump on a hot path
```

For an expensive dump on a hot path, guard it:

```python
if logger.isEnabledFor(logging.DEBUG):
    logger.debug("full state: %s", build_state_dump())
```

A format mistake never crashes a run: the sinks fall back to the raw message plus its arguments. It still produces an unreadable line, so fix the format string.

## Measurement Windows

Between the start and end of a measurement, the gate holds every record in memory:

| Allowed inside a window | Not allowed |
|---|---|
| logging calls with deferred arguments | `print`, file or network writes, database access |
| reading hardware counters | building expensive debug strings |
| appending to in memory buffers | opening connections or files |

The only console output inside a window is the gate owned status line. Resource setup (connections, files, id lookups) belongs before the window, at construction or in prepare.

## Debugging a Run

1. **Reproduce in debug mode:** `ALEMS_LOG_MODE=debug alems run <profile> 2>&1 | tee /tmp/debug.txt`.
2. **Read the per run log:** the run report prints `log file`. Filter it with `jq`:
   ```bash
   jq -r '"\(.ts) \(.level) \(.logger): \(.msg)"' <store dir>/logs/run_<run_uid>.jsonl | less
   jq -c 'select(.logger | startswith("core.readers"))' <store dir>/logs/run_<run_uid>.jsonl
   ```
3. **Follow a failed stage:** `stage_event.reason` holds the error code, `stage_event.error_ref` names the record in `<store dir>/errors/<date>/<error_ref>.json` with the traceback.
4. **Narrow to one component:** `ALEMS_LOG_COMPONENTS=readers.perf=DEBUG alems run <profile>`.
5. **Prove the window stayed clean:** `ALEMS_GATE_TRACE` plus `strace` and `scripts/tools/strace_window_check.py` (see the console output and logging guide).

## Before You Commit

```bash
python3 -m pytest tests core -q
ALEMS_LOG_MODE=debug alems run <profile> 2>&1 | grep -c "Logging error\|Traceback"     # must print 0
alems dev audit check | tail -1                                                          # 0 changes unless declared
```

Run the debug check on each execution path you touched (`save_pair`, `save_single`, `execute_goal`). Format mistakes and swallowed parse failures appear only when DEBUG lines are actually formatted.

## Verification

```bash
python3 -m pytest tests/test_no_emoji_prints.py -q
grep -rn "logger\.\(debug\|info\|warning\)(f\"" core --include=*.py | grep -v /tests/ | head
```

The first must pass; the second lists f string logging calls that should be converted to deferred arguments.

## Known Limitations

- **Component debug is file only:** `ALEMS_LOG_COMPONENTS` affects the file record, not the console.
- **Lossy log buffer inside windows:** extremely chatty components can overflow the in window log buffer; the oldest log records are dropped and counted, stage and error records never are.
- **Format strings are checked at runtime only:** a wrong `%d` is caught by the sink fallback, not by a static check.
