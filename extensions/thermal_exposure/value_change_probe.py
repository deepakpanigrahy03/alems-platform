"""
================================================================================
THERMAL EXPOSURE: SENSOR VALUE-CHANGE PROBE
================================================================================

PURPOSE:
    Read each temperature sensor's raw value as fast as possible for a set
    time and report how often the value actually changed.

WHAT THE RESULT MEANS (and does not mean):
    observed_value_change_interval is EVIDENCE of update behaviour, not the
    sensor's hardware update rate. A steady temperature, a coarse resolution
    or driver caching all produce repeated values even when the sensor did
    update. A hardware rate may be stated only from documentation of the
    sensor interface, with that source recorded.

    Three rates stay separate: the requested polling rate (sampling policy),
    the achieved polling rate (sampler_stats), and this observed
    value-change behaviour. The probe never sets a polling rate, writes
    nothing to any store, and keeps repeated readings (they are counted,
    never dropped).

Definitions match the update-rate script for energy sources: value-change
intervals with p10, p50, p90; read (arrival) intervals; failed reads.
================================================================================
"""

import logging
import statistics
import time
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

PROGRESS_EVERY_S = 10.0     # timestamped progress line interval (long-step logging rule)


class _Tracker:
    """Per-sensor counters for one probe run."""

    def __init__(self, resolution_c: Optional[float]):
        self.resolution_c = resolution_c
        self.reads = 0
        self.failed = 0
        self.last_value = None
        self.change_times: List[float] = []
        self.first_read_t: Optional[float] = None
        self.last_read_t: Optional[float] = None

    def add(self, t: float, value: Optional[float]) -> None:
        """Record one read; a change is any value different from the previous one."""
        self.reads += 1
        self.first_read_t = t if self.first_read_t is None else self.first_read_t
        self.last_read_t = t
        if value is None:
            self.failed += 1
            return
        if self.last_value is not None and value != self.last_value:
            self.change_times.append(t)
        self.last_value = value


def _quantile(values: List[float], q: float) -> Optional[float]:
    """Nearest-rank quantile; None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


def _summary(tr: _Tracker, duration_s: float) -> Dict:
    """Result dict for one sensor (seconds, rounded only for display elsewhere)."""
    gaps = [b - a for a, b in zip(tr.change_times, tr.change_times[1:])]
    span = (tr.last_read_t - tr.first_read_t) if tr.reads > 1 else None
    return {
        "probe_duration_s": duration_s,
        "n_reads": tr.reads,
        "n_failed_reads": tr.failed,
        "n_value_changes": len(tr.change_times),
        "observed_intervals_s": gaps,
        "observed_value_change_interval_s": statistics.median(gaps) if gaps else None,
        "interval_p10_s": _quantile(gaps, 0.10),
        "interval_p50_s": _quantile(gaps, 0.50),
        "interval_p90_s": _quantile(gaps, 0.90),
        "mean_read_interval_s": span / (tr.reads - 1) if span else None,
        "resolution_c": tr.resolution_c,
        "evidence_only": True,
    }


def observe_value_changes(readers: Dict[str, Callable[[], Optional[float]]],
                          duration_s: float,
                          resolutions: Optional[Dict[str, float]] = None,
                          clock: Callable[[], float] = time.monotonic) -> Dict[str, Dict]:
    """
    Read every sensor round-robin, as fast as possible, for duration_s.

    Args:
        readers:     name -> function returning the raw value in C, or None on failure.
        duration_s:  probe length in seconds.
        resolutions: name -> nominal resolution in C (reported, not used to filter).
        clock:       monotonic clock (injectable for tests).

    Returns:
        name -> summary dict (see _summary). Nothing is written anywhere.
    """
    resolutions = resolutions or {}
    trackers = {n: _Tracker(resolutions.get(n)) for n in readers}
    start = clock()
    next_log = start + PROGRESS_EVERY_S
    while clock() - start < duration_s:
        for name, fn in readers.items():
            trackers[name].add(clock(), _safe_read(fn, name))
        if clock() >= next_log:
            logger.info("value-change probe: %.0f / %.0f s, reads per sensor ~%d",
                        clock() - start, duration_s, min(t.reads for t in trackers.values()))
            next_log += PROGRESS_EVERY_S
    return {n: _summary(t, duration_s) for n, t in trackers.items()}


def _safe_read(fn: Callable[[], Optional[float]], name: str) -> Optional[float]:
    """Call one reader; a failure counts as a failed read, never stops the probe."""
    try:
        return fn()
    except Exception as exc:
        logger.debug("value-change probe: read failed for %s: %s", name, exc)
        return None
