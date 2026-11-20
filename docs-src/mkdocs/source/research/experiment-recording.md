---
**Method ID:** experiment_recording_v1
**Schema version:** core 123; extension thermal_exposure e002
**Platforms verified:** NVIDIA Grace GB10 (aarch64)
**Status:** REVIEW
**Last updated:** 2026-11-18
---

# Continuous Experiment Recording

## Overview

Per-run samplers start when a run's measurement starts and stop when it stops, so the machine
is not observed between runs: set-up, the gaps between repetitions and the cool-down after the
last run are lost, and a run cannot be compared with the temperature it started from.

Continuous experiment recording is an opt-in mode that records an experiment from a short
pre-window before its first run to the end of its last run, with an optional cool-down.
Inside a run nothing changes: the run's own samplers write to the core sample tables under the
run's `run_id`. Outside runs the recorder writes to extension tables keyed to one recording.
The recorder is fully paused during a run (no reads, no writes), so every sample is stored once
and no existing view, query or report sees anything new.

Opt in with `--record-continuous` on `run_experiment.py`, or with a `recording:` section in an
experiment config (`enabled`, `pre_window_s`, `cooldown_max_s`).

## Experiment Recording Time Line { #experiment-recording-time-line }

At recording start the wall clock and the monotonic clock are read together. Every sample time is

```latex
t_{\mathrm{sample}} = t_{\mathrm{wall,open}} + (m_{\mathrm{now}} - m_{\mathrm{open}})
```

where m is the monotonic clock, which never steps. The stored time line therefore cannot jump
when the system clock is corrected. A clock anchor (wall and monotonic time read together) is
written at open, every 10 s while recording, and at close. The anchors measure the mapping error
(wall clock minus the session time line) and detect wall-clock steps; they never change sample
times. Clock basis stored in `te_session.clock_basis`: `wall_at_open_plus_monotonic_v1`.

Each sample is stamped with the session time at the start of the read. A read that finishes after
a run has begun is discarded, so a sample can never be stored both by the recorder and by the run.

## Platform Coverage

| Platform | Architecture | Source | Canonical Role | Confidence | Status |
|---|---|---|---|---|---|
| NVIDIA Grace GB10 | aarch64 | sysfs thermal zones, cooling devices, SPBM power rails, DCGM GPU temperature | between-runs context | 0.95 | VERIFIED |
| Intel i7-1165G7 | x86_64 | sysfs thermal zones, cooling devices (no power rails) | between-runs context | 0.95 | PENDING |
| AMD Ryzen | x86_64 | sysfs thermal zones, cooling devices, NVML GPU temperature | between-runs context | 0.95 | PENDING |
| Apple M1 Pro | arm64 | none yet | - | TBD | PLANNED |

Confidence 0.95: sample times come from the monotonic clock, so they are exact relative to each
other; the absolute alignment to the wall clock is measured by the anchors (0.001 ms on GB10 over
527 s) and stays within the 5 ms target unless the clock steps. The values themselves carry the
confidence of the readers that produce them (for example 1 C resolution for GPU temperature).
To reach 1.0 the readers would need hardware timestamps for each value; sysfs and DCGM do not
provide them.

## Schema

| Table / column | Meaning |
|---|---|
| `te_session` | one row per recording; `run_id` is NULL (no row in `runs`); `exp_id` set at close from the runs inside the window |
| `te_session.pre_window_ns` | length of the pre-window |
| `te_session.workload_start_ns`, `workload_end_ns` | first run start, last run end (session time line) |
| `te_session.cooldown_status` | `not_requested`, `completed`, `timed_out` or `aborted` |
| `te_thermal_samples` | thermal zones between runs (mirrors `thermal_samples_v2`) |
| `te_cooling_samples` | cooling device state between runs (mirrors `cooling_samples`) |
| `te_gpu_temp_samples` | GPU temperature only, whole degrees (from `gpu_samples` sources) |
| `te_power_rail_samples` | power rails between runs, for the idle power reference |
| `te_ambient_samples` | room temperature; `run_id` NULL between runs; `UNAVAILABLE` until a probe is fitted |
| `te_clock_anchor` | wall and monotonic time pairs, NTP offset (`timedatectl`), stepped flag |
| `te_session_check` | per-source rate and gap checks, clock mapping error and step checks |
| `te_v_recording_thermal` | view: run samples plus between-runs samples of one recording, `owner` = `run` or `between` |

Values that could not be read are NULL with a quality flag, never 0.

## Method Provenance

| Field | Value |
|---|---|
| method_id | `experiment_recording_v1` |
| provenance | MEASURED (sample values); the time line is a documented timestamp rule |
| layer | silicon |
| formula | sample time = wall at open + monotonic elapsed (above) |
| parameters | pre-window 30 s, anchor interval 10 s, mapping limit 5 ms, cool-down tolerance 2 C for 60 s, cap configurable |

The 30 s, 2 C, 60 s and 5 ms values are engineering starting values, to be confirmed by
calibration on each platform.

## Query Reference

**Recordings and their state** — answers: which experiments were recorded continuously, how
long, and did cool-down complete? Applies to all platforms. One row per recording.

```sql
SELECT session_id, exp_id, status, cooldown_status,
       (end_ns - start_ns) / 1e9                     AS seconds,
       (workload_start_ns - start_ns) / 1e9          AS pre_window_s,
       (workload_end_ns - workload_start_ns) / 1e9   AS workload_s
FROM te_session
ORDER BY session_id DESC;
```

Example output from the NVIDIA Grace GB10 platform: `2 | 5 | closed | not_requested | 526.9 | 30.2 | 484.3`.

**No double recording** — answers: did the recorder store anything inside a run? Must be 0 for
every run. Replace 2 with a session id.

```sql
SELECT r.run_id, r.workflow_type,
       (SELECT COUNT(*) FROM te_thermal_samples t
         WHERE t.session_id = 2
           AND t.timestamp_ns >= r.start_time_ns AND t.timestamp_ns < r.end_time_ns) AS te_inside_run,
       (SELECT COUNT(*) FROM thermal_samples_v2 v WHERE v.run_id = r.run_id)      AS run_own_samples
FROM runs r
WHERE r.start_time_ns >= (SELECT start_ns FROM te_session WHERE session_id = 2)
ORDER BY r.run_id;
```

**Full thermal time line** — answers: what was the temperature before, during and between the
runs of a recording? Rows in time order, `owner` tells which source stored each sample.

```sql
SELECT owner, run_id, zone_id, timestamp_ns, temp_celsius
FROM te_v_recording_thermal
WHERE session_id = 2 AND quality_flag = 'VALID'
ORDER BY timestamp_ns;
```

**Recording health** — answers: did the recorder keep its rates, avoid long gaps and keep its
clock? Every row should be `pass`.

```sql
SELECT check_name, result, value, threshold, note
FROM te_session_check
WHERE session_id = 2
ORDER BY check_name;
```

## Verification

1. `python scripts/tools/thermal_record.py --seconds 60` records one minute with no load and
   prints rows per table and the checks; every check should be `pass`.
2. Run a small experiment with `--record-continuous` (or the example config
   `config/experiment_configs/examples/example_record_continuous.yaml`).
3. Run the four queries above: one closed recording with an `exp_id`, `te_inside_run` = 0 for
   every run, a time line with both owners, and all checks `pass`.
4. `python -m pytest -q tests/test_experiment_recorder.py` (no hardware needed).

## Known Limitations

- **Short gaps at run boundaries**: the recorder stops at a run's start boundary and resumes after the
  run's measurement stops; the few milliseconds the engine needs to start and stop its own
  samplers are covered by neither. Workaround: the gap checks report the longest gap.
- **A failed run leaves the recording paused** until the next run ends, which appears as a gap,
  never as wrong data. Workaround: none needed; the gap is reported.
- **Overlapping experiments on one machine** each store their own copy of the same machine
  readings, and the values reflect both workloads. Workaround: run one experiment per machine.
- **No energy counters between runs**: only power rails (for the idle reference), temperatures
  and cooling are recorded. Workaround: none; energy is attributed to runs only.
- **GPU temperature is whole degrees** (DCGM/NVML). Workaround: none — accept the resolution.
- **Two clock bases in one time line**: run samples keep the platform's wall-clock timestamps;
  between-runs samples use the recording's monotonic time line. They agree to within the
  measured mapping error (0.001 ms on GB10) unless the wall clock steps during an experiment,
  which the clock-step check reports. Workaround: treat a recording with a failed clock-step
  check as unaligned at run boundaries.
- **No clock anchors during runs**: anchors are written between runs only (every 10 s), so a
  clock step inside a long run is detected at the next anchor after it.
  Workaround: none needed; the step is still detected and reported.
