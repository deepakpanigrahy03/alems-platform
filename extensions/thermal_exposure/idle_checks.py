"""
Idle reference and recording checks (thermal_exposure extension).

Pure calculations on already recorded samples: no database access, no
readers, no threads. The recorder collects the samples and writes the
results; this module only decides. Keeping the rules here makes every rule
testable on synthetic data and identical across platforms.

Rules (method ids in brackets, registered in the method registry):
  * idle window values  [idle_window_median_v1]
      median and median absolute deviation (MAD) of VALID samples only.
      The median is used because short background bursts pull a mean up.
  * power from counters [idle_power_from_counter_v1]
      per-interval power = delta energy / delta time, corrected for one
      counter wraparound when the counter range is known; an interval with
      a negative delta and no known range is dropped, never guessed.
  * pre window acceptance [idle_pre_acceptance_v1]
      coverage (whole window and final interval), largest gap in the final
      interval, minimum sample count, temperature slope (upper 95 % bound of
      |slope|), relative MAD of the main power channel.
  * cooling complete [cooling_complete_v1]
      |T - median_pre| <= theta for hold_s without a gap, within max_s,
      theta = max(engineering tolerance, k * MAD_pre, sensor resolution).
  * idle drift [idle_drift_v1]
      post median - pre median per sensor and channel.

Every default below is a provisional engineering value until the
calibration sessions measure the machine; the values actually used are
stored with each recording, so they can be checked and changed later.
"""

from __future__ import annotations

import math
import statistics
from typing import Dict, List, Optional, Sequence, Tuple

# Provisional defaults. A recording config or machine profile overrides them.
# Method ids: pre_s .. power_floor_factor -> idle_pre_acceptance_v1;
# cool_* -> cooling_complete_v1; drift_* -> idle_drift_v1.
DEFAULTS: Dict[str, float] = {
    "pre_s": 600.0,                # initial pre idle window length
    "pre_max_s": 1800.0,           # longest the pre window may be extended to
    "extend_step_s": 120.0,        # extension step when not yet stable
    "final_s": 120.0,              # final interval used for stability tests
    "min_coverage": 0.98,          # share of expected VALID samples
    "min_obs_share": 0.9,          # minimum samples as share of expected
    "slope_limit_c_per_min": 0.1,  # upper 95 % bound of |slope| must be below
    "power_rel_mad_limit": 0.05,   # main channel MAD / median must be below
    "power_floor_factor": 10.0,    # median below factor * resolution: unknown
    "cool_tolerance_c": 2.0,       # engineering part of the cooling band
    "cool_mad_k": 3.0,             # noise part of the cooling band (k * MAD)
    "cool_hold_s": 60.0,           # residual must stay in band this long
    "cool_max_s": 600.0,           # longest cool-down; a timeout is not cooled
    "drift_tolerance_c": 0.5,      # temperature drift above this: drift session
    "drift_tolerance_rel": 0.05,   # power drift above this share of pre median
}

# a sample interval longer than this many expected intervals is a gap
GAP_FACTOR = 2.0

# two-sided 95 % normal quantile; the stability interval holds hundreds of
# samples at 10-20 Hz, where the t quantile equals this to two decimals
Z95 = 1.96


def median_mad(values: Sequence[float]) -> Tuple[Optional[float], Optional[float]]:
    """
    Median and median absolute deviation of the values.

    Args:
        values: VALID sample values (NULL rows already removed).

    Returns:
        (median, MAD); (None, None) when there are no values. Missing is
        None, never 0 (MIC-1).
    """
    if not values:
        return None, None
    med = statistics.median(values)
    mad = statistics.median([abs(v - med) for v in values])
    return med, mad


def interval_power_mw(samples: Sequence[Tuple[int, float]],
                      counter_range_uj: Optional[float] = None) -> List[float]:
    """
    Per-interval power from a cumulative energy counter.

    Args:
        samples: (timestamp_ns, energy_uj) pairs in time order, VALID only.
        counter_range_uj: value at which the counter wraps to zero, if known.

    Returns:
        Power in mW for each usable interval. An interval whose energy goes
        down is a wraparound: corrected when the range is known, otherwise
        dropped (a guessed value would be a made-up measurement).
    """
    out: List[float] = []
    for (t0, e0), (t1, e1) in zip(samples, samples[1:]):
        p = _one_interval_mw(t1 - t0, e1 - e0, counter_range_uj)
        if p is not None:
            out.append(p)
    return out


def _one_interval_mw(dt_ns: int, de_uj: float, counter_range_uj: Optional[float]) -> Optional[float]:
    """Power of one counter interval in mW, or None when it cannot be known."""
    if dt_ns <= 0:
        return None                            # duplicate or reordered stamp
    if de_uj < 0 and counter_range_uj is None:
        return None                            # wrap without known range: drop
    if de_uj < 0:
        de_uj += counter_range_uj              # one wraparound
    return de_uj / dt_ns * 1e6                 # uJ per ns -> W is 1e3; -> mW is 1e6


def max_gap_ns(times_ns: Sequence[int]) -> Optional[int]:
    """Largest distance between consecutive sample times, or None (< 2 samples)."""
    if len(times_ns) < 2:
        return None
    return max(b - a for a, b in zip(times_ns, times_ns[1:]))


def slope_c_per_min(times_ns: Sequence[int], temps: Sequence[float],
                    resolution_c: float = 0.0
                    ) -> Tuple[Optional[float], Optional[float]]:
    """
    Least-squares temperature slope and the upper 95 % bound of |slope|.

    Repeated (quantised) readings are kept as samples; with a coarse sensor
    a flat trace gives slope 0 with a wide bound, which is why the bound,
    not the slope, decides stability.

    Returns:
        (slope, upper bound of |slope|) in C per minute, or (None, None)
        with fewer than 3 samples or no time spread.
    """
    n = len(times_ns)
    if n < 3 or n != len(temps):
        return None, None
    x = [(t - times_ns[0]) / 60e9 for t in times_ns]   # minutes
    mx, my = sum(x) / n, sum(temps) / n
    sxx = sum((xi - mx) ** 2 for xi in x)
    if sxx <= 0:
        return None, None
    b = sum((xi - mx) * (yi - my) for xi, yi in zip(x, temps)) / sxx
    resid = sum((yi - my - b * (xi - mx)) ** 2 for xi, yi in zip(x, temps))
    se = math.sqrt(resid / (n - 2) / sxx)
    # resolution floor: a sensor of resolution r cannot show a change below r
    # over the interval, so the bound is never smaller than r / span
    floor = resolution_c / (x[-1] - x[0]) if x[-1] > x[0] else 0.0
    return b, max(abs(b) + Z95 * se, floor)


def cooling_band(mad_pre: Optional[float], resolution_c: float, cfg: Dict[str, float]) -> float:
    """
    Cooling band half-width theta for one sensor.

    theta = max(engineering tolerance, k * MAD_pre, resolution). The
    resolution term keeps a zero MAD (quantised sensor) from giving an
    impossible band; the MAD term widens the band for noisy sensors.
    """
    noise = cfg["cool_mad_k"] * mad_pre if mad_pre is not None else 0.0
    return max(cfg["cool_tolerance_c"], noise, resolution_c)


class CoolingHold:
    """
    Cooling-complete state for one decision sensor plus its guard sensors.

    Feed every VALID sample of the decision and guard sensors in time order.
    Cooling is complete once every sensor has stayed inside its own band
    (absolute residual, so an undershoot does not pass) for hold_s with no
    sample gap longer than GAP_FACTOR expected intervals. A gap or a sample
    outside the band restarts the hold. A timeout is never complete.
    """

    def __init__(self, bands: Dict[str, Tuple[float, float, float]], hold_s: float) -> None:
        """
        Args:
            bands: sensor -> (pre median, band theta, expected interval in s).
            hold_s: seconds every sensor must stay in band.
        """
        self.bands = dict(bands)
        self.hold_ns = int(hold_s * 1e9)
        self._last_ns: Dict[str, Optional[int]] = {s: None for s in bands}
        self._inside: Dict[str, bool] = {s: False for s in bands}
        self._since: Optional[int] = None      # start of the current clean hold
        self.resets = 0                        # reported: how often the hold restarted

    def feed(self, sensor: str, ts_ns: int, value: float) -> bool:
        """Add one VALID sample; returns True once cooling is complete."""
        if sensor not in self.bands:
            return self.complete(ts_ns)
        med, theta, interval_s = self.bands[sensor]
        last = self._last_ns[sensor]
        gap = last is not None and ts_ns - last > GAP_FACTOR * interval_s * 1e9
        self._last_ns[sensor] = ts_ns
        self._inside[sensor] = abs(value - med) <= theta
        if gap or not self._inside[sensor]:
            self._restart(None)
        elif all(self._inside.values()) and self._since is None:
            self._since = ts_ns                # every sensor in band from here
        return self.complete(ts_ns)

    def complete(self, now_ns: int) -> bool:
        """True when every sensor has been in band for the whole hold."""
        return self._since is not None and now_ns - self._since >= self.hold_ns

    def _restart(self, ts_ns: Optional[int]) -> None:
        """Restart the hold (gap or out of band)."""
        if self._since is not None:
            self.resets += 1
        self._since = ts_ns


def coverage(n_valid: int, duration_s: float, hz: float) -> Optional[float]:
    """Share of expected samples that are VALID; None when nothing was expected."""
    expected = duration_s * hz
    if expected <= 0:
        return None
    return min(1.0, n_valid / expected)


def assess_series(times_ns: Sequence[int], window: Tuple[int, int], hz: float,
                  cfg: Dict[str, float]) -> Dict[str, object]:
    """
    Coverage and gap tests for one series of VALID samples in a window.

    Returns a dict with coverage (whole and final), largest final gap in ms,
    sample count and a list of failure reasons (empty when all pass).
    """
    start, end = window
    final_start = max(start, end - int(cfg["final_s"] * 1e9))
    final = [t for t in times_ns if final_start <= t < end]
    dur_s, fin_s = (end - start) / 1e9, (end - final_start) / 1e9
    cov, cov_f = coverage(len(times_ns), dur_s, hz), coverage(len(final), fin_s, hz)
    edges = [final_start] + final + [end]       # a gap at either edge counts too
    gap = max_gap_ns(edges)
    reasons: List[str] = []
    if cov is None or cov < cfg["min_coverage"]:
        reasons.append("coverage %s" % _fmt(cov))
    if cov_f is None or cov_f < cfg["min_coverage"]:
        reasons.append("final coverage %s" % _fmt(cov_f))
    if gap is not None and gap > GAP_FACTOR * 1e9 / hz:
        reasons.append("final gap %.0f ms" % (gap / 1e6))
    if len(times_ns) < cfg["min_obs_share"] * dur_s * hz:
        reasons.append("only %d samples" % len(times_ns))
    return {"coverage": cov, "final_coverage": cov_f, "n": len(times_ns),
            "final_gap_ms": gap / 1e6 if gap is not None else None, "reasons": reasons}


def assess_stability(times_ns: Sequence[int], temps: Sequence[float], resolution_c: float,
                     powers_mw: Sequence[float], power_resolution_mw: float,
                     cfg: Dict[str, float]) -> Dict[str, object]:
    """
    Stability tests over the final interval: decision sensor slope bound and
    main channel relative MAD. A test that cannot be made is 'unknown'.
    """
    slope, bound = slope_c_per_min(times_ns, temps, resolution_c)
    med, mad = median_mad(powers_mw)
    rel = None
    if med is not None and med >= cfg["power_floor_factor"] * power_resolution_mw:
        rel = mad / med                         # undefined near zero power
    slope_ok = None if bound is None else bound < cfg["slope_limit_c_per_min"]
    power_ok = None if rel is None else rel < cfg["power_rel_mad_limit"]
    return {"slope_c_per_min": slope, "slope_bound": bound, "slope_ok": slope_ok,
            "power_rel_mad": rel, "power_ok": power_ok}


def drift(pre: Optional[float], post: Optional[float], tolerance: float,
          relative: bool = False) -> Tuple[str, Optional[float]]:
    """
    Idle drift result: ('pass' | 'warn' | 'unknown', post - pre).

    'warn' marks a drift session (kept, analysed separately); 'unknown' when
    either window is missing. With relative=True the tolerance is a share
    of the pre median (power channels).
    """
    if pre is None or post is None:
        return "unknown", None
    diff = post - pre
    limit = tolerance * abs(pre) if relative else tolerance
    return ("warn" if abs(diff) > limit else "pass"), diff


def _fmt(v: Optional[float]) -> str:
    """Short text for an optional share."""
    return "n/a" if v is None else "%.3f" % v
