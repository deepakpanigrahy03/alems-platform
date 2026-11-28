---
**Method ID:** thermal_safety_guard_v1
**Schema version:** 133 (safety_guard_episodes, safety_guard_events, safety_guard_samples)
**Platforms verified:** NVIDIA Grace GB10 (aarch64)
**Status:** REVIEW
**Last updated:** 2026-11-25
---

# Safety Guard

## Overview

The safety guard protects the hardware during experiments by controlling when work starts. Before
every run (both workflows of a paired run, single runs, every retry attempt and sequencer steps) it
waits until the hardware is cool, and while an experiment is open it watches the thermal headroom
once per second and records what it sees. It never aborts a run and never ends an experiment on its
own: it only waits, so long campaigns slow down when the machine runs warm instead of stopping. Inside
a run, protection is left to the hardware's own slowdown and shutdown limits, whose activity is
recorded. The guard is part of the platform, independent of extensions, and applies to every
execution path.

**Headroom** is the margin in °C between a component's temperature and a limit that the component
itself reports. For NVIDIA GPUs it is the slowdown margin that `nvidia-smi` shows as `T.Limit`.
The guard uses the smallest headroom over all available sources, so the hottest part decides.

The guard is a safety mechanism, not a measurement of exposure: its records show what the guard saw
and decided, so a stopped or refused experiment can be explained afterwards.

## Safety Guard Rules { #safety-guard-rules }

Rules (all numbers come from `safety.thermal` in `config/app_settings.yaml`; there are no defaults in
code, and a missing or invalid key makes the guard refuse to start):

| Rule | What happens |
| --- | --- |
| No source | No headroom source on the machine: the experiment is refused unless `allow_without_source` is true |
| Rest | Each run starts at least `min_rest_s` after the previous run ended; a retry backoff can lengthen this, never shorten it |
| Start gate | Headroom must stay at or above the start level for `start_stable_s` on fresh readings; the wait has no limit |
| Long wait | Every `max_wait_s` of waiting a warning is recorded (`idle_headroom_insufficient` before the first run, `gate_timeout` afterwards); the wait continues |
| Start level | `max(start_floor_headroom_c, idle_headroom - start_idle_margin_c)`; while the margin is null, the floor alone; with a margin set and no valid idle reference, refused |
| Watchdog | One reading per `interval_s` for the whole experiment, also between runs |
| Stop level | Headroom below `stop_headroom_c` during a run is recorded once per crossing (`warn`, `stop_level_crossed`); the run completes and the next run waits at the gate |
| Silent source | A required source silent for longer than `stale_after_s` is recorded (`source_stale`) and blocks admission until it answers again |
| Rate of rise | Recorded per reading; a warning only |

```latex
H(t) = \min_{s \in S_{\mathrm{required}}} h_s(t), \qquad
H_{\mathrm{start}} = \max\left(H_{\mathrm{floor}},\ H_{\mathrm{idle}} - k\right), \qquad
\mathrm{crossing} \iff H(t) < H_{\mathrm{stop}}
```

A source that stops answering never improves the minimum: its last value stays a candidate for at
most `stale_after_s`, after which the guard is blind. Being blind blocks admission.

Sources: the NVIDIA slowdown margin (`slowdown_margin`) and each Linux thermal zone against its own
critical trip point (`critical_trip`). The two are different measurements and are stored with their
type. A zone contributes only when it reports a readable critical trip above its current temperature;
otherwise it is recorded as `invalid` and never decides the minimum.

Every decision is written first as one line to the host log (`alems.jsonl`, forced to disk) and then
to the store. Store writes happen only between runs, so the guard adds no store I/O inside a
measurement window.

## Platform Coverage

| Platform | Architecture | Source | Canonical Role | Confidence | Status |
|----------|-------------|--------|---------------|------------|--------|
| NVIDIA Grace GB10 | aarch64 | NVML slowdown margin (T.Limit) | GPU | 0.90 | VERIFIED |
| Intel x86_64 | x86_64 | thermal zone critical trip point | CPU_PACKAGE | TBD | PENDING |
| Apple M4 | arm64 | none known without elevated rights | none | TBD | PLANNED |
| AMD Ryzen | x86_64 | thermal zone critical trip point | CPU_PACKAGE | TBD | PENDING |

On machines without a supported source the guard refuses to start (rule "No source").

## Schema

| Table | One row per | Main columns |
| --- | --- | --- |
| `safety_guard_episodes` | guard activation (process) | `episode_id`, `entry_path`, start and end (UTC and monotonic ns), `policy_version`, `config_json`, `config_sha256`, `status` (running, ended, crashed), `end_reason`; `latched` and `latch_reason` are reserved and stay 0 and NULL under the current policy |
| `safety_guard_events` | decision or state change | `event_id` (same id as the host log line), `episode_id`, `seq`, `event_type`, time, `value_c`, `threshold_c`, `source`, `component`, `limit_type`, `quality`, links (`exp_id`, `run_id`, `goal_id`, `attempt_no`, `seq_attempt_id`, `te_session_id`), `detail_json`, `log_written` |
| `safety_guard_samples` | watchdog reading | `episode_id`, `seq`, time, `min_headroom_c`, deciding source, `min_quality`, `gpu_temp_c`, `gpu_power_mw`, `gpu_power_limit_mw`, `clock_reasons_bitmask`, `rise_c_per_s`, `readings_json` |

Quality words: fresh, stale, unavailable, failed, invalid. Missing values are NULL, never 0. Links to
runs are plain ids without foreign keys, because an admission decision happens before the run row
exists. The sample table grows by one row per second while the guard runs; nothing is deleted by
code.

## Method Provenance

| Field | Value |
| --- | --- |
| method_id | `thermal_safety_guard_v1` |
| provenance | CALCULATED (minimum over measured margins) |
| layer | silicon |
| confidence | 0.90 |

Confidence 0.90: the NVML margin is reported in whole degrees and read once per second, so the guard
can see a crossing up to one interval late and up to 1 °C early or late. Under steady inference this
does not change an admission decision made with a start level well above the stop level; for
sub-second power steps a short crossing can be missed. To reach 1.0 the margin would need a finer resolution and a guaranteed
update rate documented by the vendor. The guard records what it saw; it does not claim the hardware
was safe between readings.

## Query Reference

**Guard episodes and how they ended.** Answers: did the guard refuse, stop or finish normally?
Applies to every platform. One row per episode.

```sql
SELECT episode_id, entry_path, status, latched, latch_reason, end_reason,
       (ended_utc_ns - started_utc_ns) / 1e9 AS seconds
FROM safety_guard_episodes
ORDER BY started_utc_ns;
```

**Decisions of the latest episode.** Answers: what did the guard decide, in order, and was each
decision also written to the host log? `log_written` is 1 when the log line was written.

```sql
SELECT seq, event_type, value_c, threshold_c, quality, log_written
FROM safety_guard_events
WHERE episode_id = (SELECT episode_id FROM safety_guard_episodes ORDER BY started_utc_ns DESC LIMIT 1)
ORDER BY seq;
```

**Headroom trace of the latest episode.** Answers: how close did the hardware come to its limit?
Typical GB10 values with a model server loaded: 20 to 38 °C.

```sql
SELECT seq, utc_ns, min_headroom_c, min_component, min_quality
FROM safety_guard_samples
WHERE episode_id = (SELECT episode_id FROM safety_guard_episodes ORDER BY started_utc_ns DESC LIMIT 1)
ORDER BY seq;
```

**Long waits, stop-level crossings and silent sources.** Answers: when did the guard hold work back,
and did any run come close to the limit? A crossing is a `warn` event whose detail says
`stop_level_crossed`.

```sql
SELECT episode_id, event_type, value_c, threshold_c, detail_json, utc_ns
FROM safety_guard_events
WHERE event_type IN ('warn', 'source_stale', 'idle_headroom_insufficient', 'gate_timeout')
ORDER BY utc_ns;
```

**Admission before each run.** Answers: which gate opening admitted each run, and at what headroom?
Joined by time, because admission happens before the run row exists.

```sql
SELECT r.run_id, MAX(e.utc_ns) AS gate_open_ns
FROM runs r
JOIN safety_guard_events e ON e.event_type = 'gate_open' AND e.utc_ns <= r.start_time_ns
GROUP BY r.run_id
ORDER BY r.run_id;
```

## Verification

1. Settings load: `python -c "import yaml; from core.safety.thermal_guard import load_config; print(load_config(yaml.safe_load(open('config/app_settings.yaml'))['safety']['thermal']))"` prints the limits.
2. Unit tests: `python -m pytest -q tests/test_safety_guard.py tests/test_safety_hook.py` passes.
3. One run on a sandbox store, then the episode query above shows an episode with status `ended`,
   the decisions query shows `episode_start`, `gate_open` before each run and `episode_end`, all with
   `log_written` = 1.
4. The host log `alems.jsonl` in the log directory holds one line per event with the same `event_id`.
5. `python scripts/test_exp_integrity.py --latest` reports the safety guard checks.

## Known Limitations

- **No in-run intervention**: the guard never interrupts a run; a run that heats past the stop level completes, and only the next run waits.
  Workaround: none by design — the hardware's own slowdown and shutdown act inside a run; crossings are recorded so analysis can see or exclude those runs.
- **Readings between ticks**: the guard reads once per `interval_s`; a crossing shorter than one interval can be missed.
  Workaround: none — the gate keeps every run's start well above the stop level.
- **Whole degree margin**: NVML reports the margin in whole degrees.
  Workaround: none — accept the resolution.
- **Cause of power loss**: the guard watches temperature margins; it cannot prevent a power loss caused by power delivery or firmware.
  Workaround: electrical readings are recorded as evidence only.
- **Machines without a source**: without a supported headroom source the guard refuses to start.
  Workaround: `allow_without_source: true`, which runs unguarded and is recorded.
- **Guard-paced back-to-back**: with a minimum rest between runs, a "back to back" schedule means zero extra delay beyond the guard's rest, not zero gap.
  Workaround: record requested and actual gaps separately.
- **GPU temperature and power in samples**: `gpu_temp_c`, `gpu_power_mw` and `gpu_power_limit_mw` are NULL in this version; the per-run GPU tables hold those readings.
  Workaround: join by time with the run's GPU samples.
- **Provisional limits**: the start floor, stop level, rest, stability time and stale limit are provisional values for attended validation.
  Workaround: none — change only with a recorded decision; the values in force are stored with every episode.
