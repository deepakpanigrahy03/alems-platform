"""
Thermal sensor probe (A-LEMS SDK).

Reads temperature sensors repeatedly for a set time and reports what was
OBSERVED: value steps, value-change intervals, read latency and failures.
Shared by core discovery (scripts/detect_thermal.py) and extensions, so it
lives in the SDK, which both may import.

What the results mean (and do not mean):
  * observed_value_step_c is the smallest non-zero step seen at least twice.
    It is the practical granularity of the acquisition path, not the
    sensor's accuracy and not proof of its physical resolution.
  * value-change intervals are evidence of update behaviour, not the
    hardware update rate: a steady temperature or caching also repeats
    values. A hardware rate may only come from documentation.
  * a sensor that never changed gets status insufficient_transitions and a
    NULL step; it never gets 0.
  * possible_duplicate_of is advisory: two inputs moved identically during
    the probe. Nothing is merged or dropped.
Nothing is written anywhere by this module.
"""

from __future__ import annotations

import logging
import statistics
import time
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

PROGRESS_EVERY_S = 10.0      # timestamped progress line (long-step logging rule)
MIN_STEP_REPEATS = 2         # a step counts only if seen at least this often
MIN_INTERVAL_CHANGES = 5     # interval percentiles need at least this many changes
STEP_DECIMALS = 6            # float noise below 1e-6 C is not a step
INTERRUPT_FACTOR = 5.0       # a round slower than this x the period is an interruption

ReadRound = Callable[[], Dict[str, Optional[float]]]


def _quantile(values: List[float], q: float) -> Optional[float]:
    """Nearest-rank quantile; None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))]


class _Series:
    """Per-sensor observations of one probe."""

    def __init__(self) -> None:
        self.reads = 0
        self.failed = 0
        self.values: List[Tuple[float, float]] = []   # (time, value) of successful reads
        self.change_times: List[float] = []
        self.steps: Dict[float, int] = {}

    def add(self, t: float, value: Optional[float]) -> None:
        """One read; a change is a value different from the previous successful one."""
        self.reads += 1
        if value is None:
            self.failed += 1
            return
        if self.values and value != self.values[-1][1]:
            self.change_times.append(t)
            step = round(abs(value - self.values[-1][1]), STEP_DECIMALS)
            self.steps[step] = self.steps.get(step, 0) + 1
        self.values.append((t, value))


def _step(steps: Dict[float, int]) -> Tuple[Optional[float], str]:
    """Smallest step seen MIN_STEP_REPEATS times, with its status."""
    reliable = [s for s, n in steps.items() if s > 0 and n >= MIN_STEP_REPEATS]
    if reliable:
        return min(reliable), "measured"
    return None, "insufficient_transitions"


def _series_summary(s: _Series) -> Dict:
    """Result for one sensor."""
    gaps = [b - a for a, b in zip(s.change_times, s.change_times[1:])]
    enough = len(s.change_times) >= MIN_INTERVAL_CHANGES
    step, status = _step(s.steps)
    vals = [v for _, v in s.values]
    return {"n_reads": s.reads, "n_failed_reads": s.failed,
            "n_value_changes": len(s.change_times),
            "observed_value_step_c": step, "resolution_status": status,
            "interval_p10_s": _quantile(gaps, 0.10) if enough else None,
            "interval_p50_s": _quantile(gaps, 0.50) if enough else None,
            "interval_p90_s": _quantile(gaps, 0.90) if enough else None,
            "min_c": min(vals) if vals else None, "max_c": max(vals) if vals else None,
            "median_c": statistics.median(vals) if vals else None}


def _duplicates(series: Dict[str, _Series]) -> Dict[str, List[str]]:
    """Sensors whose value sequences were identical and changed at least once."""
    keyed: Dict[tuple, List[str]] = {}
    for name, s in series.items():
        if s.change_times:
            keyed.setdefault(tuple(v for _, v in s.values), []).append(name)
    out: Dict[str, List[str]] = {}
    for group in (g for g in keyed.values() if len(g) > 1):
        for name in group:
            out[name] = sorted(n for n in group if n != name)
    return out


def probe(read_rounds: Dict[str, ReadRound], duration_s: float, hz: float = 50.0,
          clock: Callable[[], float] = time.monotonic,
          sleep: Callable[[float], None] = time.sleep) -> Dict:
    """
    Probe several sources for duration_s at about hz rounds per second.

    Args:
        read_rounds: source name -> callable returning {sensor id: C or None}
                     for all sensors of that source in one read.
        duration_s:  probe length in seconds.
        hz:          target rounds per second (paced, so the probe itself
                     does not heat the machine by spinning a core).

    Returns:
        {"sensors": {id: summary}, "sources": {name: latency summary},
         "probe": {achieved duration, rounds, interruptions}}
    """
    period, series = 1.0 / hz, {}
    lat: Dict[str, List[float]] = {n: [] for n in read_rounds}
    errors: Dict[str, Dict[str, int]] = {n: {} for n in read_rounds}
    start = clock()
    rounds, interruptions, next_log, last_round = 0, 0, start + PROGRESS_EVERY_S, start
    while clock() - start < duration_s:
        t_round = clock()
        interruptions += int(rounds > 0 and t_round - last_round > INTERRUPT_FACTOR * period)
        last_round = t_round
        for name, fn in read_rounds.items():
            _one_source(name, fn, clock, series, lat[name], errors[name])
        rounds += 1
        next_log = _progress(clock, start, duration_s, rounds, next_log)
        sleep(max(0.0, period - (clock() - t_round)))
    achieved = clock() - start
    return {"sensors": _sensor_results(series),
            "sources": {n: _latency(lat[n], errors[n]) for n in read_rounds},
            "probe": {"requested_s": duration_s, "achieved_s": achieved, "rounds": rounds,
                      "target_hz": hz, "interruptions": interruptions}}


def _one_source(name: str, fn: ReadRound, clock: Callable[[], float],
                series: Dict[str, _Series], lat: List[float], errors: Dict[str, int]) -> None:
    """One read of one source; a failure is counted by error type, never raised."""
    t0 = clock()
    try:
        values = fn() or {}
    except Exception as exc:
        errors[type(exc).__name__] = errors.get(type(exc).__name__, 0) + 1
        values = {}
    t1 = clock()
    lat.append(t1 - t0)
    for sid, v in values.items():
        series.setdefault(sid, _Series()).add(t1, v)


def _sensor_results(series: Dict[str, _Series]) -> Dict[str, Dict]:
    """Per-sensor summaries plus advisory duplicates."""
    dups = _duplicates(series)
    out = {}
    for sid, s in series.items():
        out[sid] = _series_summary(s)
        out[sid]["possible_duplicate_of"] = dups.get(sid, [])
    return out


def _latency(lat: List[float], errors: Dict[str, int]) -> Dict:
    """Read latency of one source in ms, and failures by error type."""
    ms = [x * 1e3 for x in lat]
    return {"n_reads": len(ms), "latency_ms_min": min(ms) if ms else None,
            "latency_ms_median": statistics.median(ms) if ms else None,
            "latency_ms_p90": _quantile(ms, 0.90), "latency_ms_max": max(ms) if ms else None,
            "errors": dict(errors)}


def _progress(clock: Callable[[], float], start: float, duration_s: float,
              rounds: int, next_log: float) -> float:
    """Timestamped progress line every PROGRESS_EVERY_S seconds."""
    if clock() < next_log:
        return next_log
    logger.info("thermal probe: %.0f / %.0f s, %d rounds", clock() - start, duration_s, rounds)
    return next_log + PROGRESS_EVERY_S


# ── compatibility: the earlier per-sensor value-change probe ─────────────────

def observe_value_changes(readers: Dict[str, Callable[[], Optional[float]]],
                          duration_s: float,
                          resolutions: Optional[Dict[str, float]] = None,
                          clock: Callable[[], float] = time.monotonic) -> Dict[str, Dict]:
    """
    Earlier API (unchanged behaviour): read every sensor round-robin as fast
    as possible and report value-change intervals per sensor.
    """
    resolutions = resolutions or {}
    tr = {n: {"reads": 0, "failed": 0, "last": None, "changes": [], "first": None, "t": None}
          for n in readers}
    start, next_log = clock(), clock() + PROGRESS_EVERY_S
    while clock() - start < duration_s:
        for name, fn in readers.items():
            _legacy_add(tr[name], clock(), _safe(fn, name))
        if clock() >= next_log:
            logger.info("value-change probe: %.0f / %.0f s", clock() - start, duration_s)
            next_log += PROGRESS_EVERY_S
    return {n: _legacy_summary(t, duration_s, resolutions.get(n)) for n, t in tr.items()}


def _safe(fn: Callable[[], Optional[float]], name: str) -> Optional[float]:
    """Call one reader; a failure counts as a failed read."""
    try:
        return fn()
    except Exception as exc:
        logger.debug("value-change probe: read failed for %s: %s", name, exc)
        return None


def _legacy_add(t: Dict, now: float, value: Optional[float]) -> None:
    """Record one read in the earlier tracker shape."""
    t["reads"] += 1
    t["first"] = now if t["first"] is None else t["first"]
    t["t"] = now
    if value is None:
        t["failed"] += 1
        return
    if t["last"] is not None and value != t["last"]:
        t["changes"].append(now)
    t["last"] = value


def _legacy_summary(t: Dict, duration_s: float, resolution: Optional[float]) -> Dict:
    """Result dict in the earlier shape."""
    gaps = [b - a for a, b in zip(t["changes"], t["changes"][1:])]
    span = (t["t"] - t["first"]) if t["reads"] > 1 else None
    return {"probe_duration_s": duration_s, "n_reads": t["reads"], "n_failed_reads": t["failed"],
            "n_value_changes": len(t["changes"]), "observed_intervals_s": gaps,
            "observed_value_change_interval_s": statistics.median(gaps) if gaps else None,
            "interval_p10_s": _quantile(gaps, 0.10), "interval_p50_s": _quantile(gaps, 0.50),
            "interval_p90_s": _quantile(gaps, 0.90),
            "mean_read_interval_s": span / (t["reads"] - 1) if span else None,
            "resolution_c": resolution, "evidence_only": True}
