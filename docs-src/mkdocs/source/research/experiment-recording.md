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

## Recording Spans { #recording-spans }

A recording is cut into labelled time intervals called spans. A span has a kind, a start, an end, a parent and typed attributes, and it is stored in the existing span tables under the vocabulary `thermal_session` (version 1). Analysis code selects samples by span instead of rebuilding the boundaries from run times.

| Kind | Parent | Covers | Written by |
|---|---|---|---|
| `session` | none | the whole recording | recorder |
| `idle_window` (purpose `pre`) | session | open to the first run start | recorder |
| `workload` | session | first run start to last run end | recorder |
| `run_slot` | workload | one run (recorder pause to resume) | recorder |
| `gap` | workload | an unplanned wait between two runs | recorder |
| `cooling_tail` | session | last run end to the close of the recording | recorder |
| `warmup`, `idle_window` (purpose `post`) | session | planned warm-up and post idle period | session runner |
| `goal_slot`, `pause`, `calibration_step` | workload | goal attempts, planned pauses, load steps | session runner |

**Boundaries.** Every boundary is an instant the recorder already records on its time line: the open, the pause at each run start, the resume at each run end, and the close. The top-level spans cover the session end to start with no gap and no overlap, and run slots and gaps cover the workload the same way. A recording with no run has only the pre-window. When no cool-down was requested, the time between the last run and the close is still a `cooling_tail`, with `status = not_requested`.

**Link to runs.** A run receives its `run_id` only when it is saved, after its measurement has stopped. The recorder therefore links each run slot at the close of the recording to the one `runs` row whose `start_time_ns` lies within 5 ms of the slot start (`run_match = matched`). With no such row or with more than one, `run_id` is absent and `run_match` is `none` or `ambiguous`.

**Writing and crashes.** Spans are written by the same writer and the same flush as the between-runs samples, never during a run. If the process dies, the next recording start marks the old recording `aborted` and, in the same transaction, closes its open spans with status `error` (end = last stored sample, unless the end was already known).

**Vocabulary contract.** The vocabulary declares each kind's allowed parents, its attributes, their types and allowed values, and which attributes are required. A span with an unknown kind, a wrong parent, a wrong attribute type, an undeclared attribute or a missing required attribute is refused before it is stored.

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

### Recording spans

No new table. Rows in existing tables:

| Table | Rows | Meaning |
|---|---|---|
| `spans` | `vocabulary = 'thermal_session'`, `run_id` NULL, `trace_id` = `te_session.global_session_id` | one row per part of the recording; `start_ns`, `end_ns` on the recording time line |
| `span_attributes` | typed attributes per kind (`value_type` string, int, float, bool, json) | for example `purpose`, `planned_s`, `actual_s`, `run_id`, `run_match`, `status` |
| `span_placements` | one per span, `node` = logical node name, `device = 'system'` | which machine the span belongs to; never a hostname |
| `te_session_check` | `spans_partition`, `spans_run_match` | structure check (fail on any gap, overlap or open span) and slot linking (warn if a slot has no unique run) |

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

**`recording_spans_v1`** (MEASURED, confidence 0.95, layer silicon). Span boundaries are the recorder's own pause and resume instants on the recording time line, so they carry the time line's uncertainty. Confidence 0.95, not 1.0: the boundaries share the time line's mapping error (measured about 0.001 ms on NVIDIA Grace GB10, limit 5 ms) and a wall-clock step during a recording shifts run times relative to span times; slot linking can fail (reported, never guessed). It would reach 1.0 if runs were stamped from the same time line as the recorder. For sample selection the effect is below one thermal sample interval (50 ms at 20 Hz).

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

### Recording spans

**Parts of the latest recording.** Answers: which parts did the recording have, and how long was each? Applies to all platforms with recording. Expected: one session, one pre-window, one workload, one run slot per run, gaps between runs, one cooling tail.
```sql
SELECT s.kind, s.name, ROUND((s.end_ns - s.start_ns) / 1e9, 3) AS seconds, s.status
FROM spans s JOIN te_session t ON s.trace_id = t.global_session_id
WHERE t.session_id = (SELECT MAX(session_id) FROM te_session)
  AND s.vocabulary = 'thermal_session'
ORDER BY s.start_ns, s.kind;
```

**Mean temperature per part outside runs.** Answers: how warm was each zone in the pre-window, in each gap and in the cooling tail? Uses only between-runs samples (run samples stay in the run tables). Expected: one row per part and zone, values in degrees C.
```sql
SELECT s.kind, s.name, x.zone_id, ROUND(AVG(x.temp_celsius), 2) AS mean_c, COUNT(*) AS n
FROM spans s
JOIN te_session t ON s.trace_id = t.global_session_id
JOIN te_thermal_samples x ON x.session_id = t.session_id
     AND x.timestamp_ns >= s.start_ns AND x.timestamp_ns < s.end_ns
WHERE t.session_id = (SELECT MAX(session_id) FROM te_session)
  AND s.vocabulary = 'thermal_session'
  AND s.kind IN ('idle_window', 'gap', 'cooling_tail')
  AND x.quality_flag = 'VALID'
GROUP BY s.span_id, x.zone_id
ORDER BY s.start_ns, x.zone_id;
```

**Run slot to run.** Answers: which `runs` row is each run slot? Expected: one row per slot, `run_match = matched`.
```sql
SELECT s.name,
       MAX(CASE WHEN a.key = 'run_match' THEN a.value_text END) AS run_match,
       MAX(CASE WHEN a.key = 'run_id' THEN a.value_text END) AS run_id
FROM spans s JOIN te_session t ON s.trace_id = t.global_session_id
JOIN span_attributes a ON a.span_id = s.span_id
WHERE t.session_id = (SELECT MAX(session_id) FROM te_session)
  AND s.kind = 'run_slot'
GROUP BY s.span_id ORDER BY s.start_ns;
```

## Verification

1. `python scripts/tools/thermal_record.py --seconds 60` records one minute with no load and
   prints rows per table and the checks; every check should be `pass`.
2. Run a small experiment with `--record-continuous` (or the example config
   `config/experiment_configs/examples/example_record_continuous.yaml`).
3. Run the four queries above: one closed recording with an `exp_id`, `te_inside_run` = 0 for
   every run, a time line with both owners, and all checks `pass`.
4. `python -m pytest -q tests/test_experiment_recorder.py` (no hardware needed).

For spans, after a recorded experiment:
```bash
python scripts/test_exp_integrity.py --latest      # span structure and placements
sqlite3 "$DB" "SELECT check_name, result, value, note FROM te_session_check
  WHERE session_id = (SELECT MAX(session_id) FROM te_session) AND check_name LIKE 'spans_%';"
```
Expected: `spans_partition` pass; `spans_run_match` pass when every run was saved.

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
- **Spans from the session runner not yet written**: warm-up, post idle window, goal slots, planned pauses and calibration steps are defined in the vocabulary but written only once a session runner produces them.
  Workaround: None — the recorder's spans cover pre-window, runs, gaps and the tail.
- **No link to agent goal spans**: agent spans are stamped with a per-process counter, not the recording time line, so they cannot be aligned with recording spans.
  Workaround: link through run slots and `run_id`.
- **Logical node name**: recordings opened by the standard entry use the node name `local`.
  Workaround: None until a configured node name is passed; it is never a hostname.
- **Recordings made before spans existed** have no spans; the integrity checker reports a warning.
  Workaround: use `te_session` boundary columns for those recordings.
