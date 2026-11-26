"""
Window contract: one set of rules for turning reader data into window values.

Every reader declares, per metric, how its values must be combined over a time
window. The rules below are the only place that arithmetic lives, so a new
reader on any platform gets correct pre, task and post values by declaring its
metrics, without code of its own.

Metric representations:
    CUMULATIVE        counter that only grows (energy, CPU ticks): value over a
                      window = difference of the counter at its two boundaries.
    INTERVAL_COUNT    amount per sample interval (bytes, interrupts, perf counts):
                      a straddling interval is split by overlap, marked estimated.
    INTERVAL_AVERAGE  mean rate over each sample interval (power, frequency):
                      weighted by overlap time; power x seconds gives joules.
    POINT             instantaneous observation (temperature): only samples
                      stamped inside the window count.

Windows are half open, [start, end): a sample or event exactly at `end`
belongs to the next window, so adjacent windows never double count.
Unknown is None, never 0 (MIC-1). Nothing here raises on bad data.
"""

from typing import Dict, Iterable, List, NamedTuple, Optional

CUMULATIVE = "cumulative"
INTERVAL_COUNT = "interval_count"
INTERVAL_AVERAGE = "interval_average"
POINT = "point"
REPRESENTATIONS = (CUMULATIVE, INTERVAL_COUNT, INTERVAL_AVERAGE, POINT)

SNAPSHOT = "snapshot"
SERIES = "series"


class SeriesSample(NamedTuple):
    """One timestamped sample; a point observation has start_ns == end_ns."""
    start_ns: int
    end_ns: int
    values: Dict[str, Optional[float]]


class WindowValue(NamedTuple):
    """
    A value over one window.

    value:     the result, or None when it cannot be established.
    coverage:  share of the window covered by evidence, 0.0 to 1.0 (None if n/a).
    estimated: True when any part was apportioned rather than observed.
    reason:    why value is None or estimated; empty when observed in full.
    """
    value: Optional[float]
    coverage: Optional[float]
    estimated: bool
    reason: str


def _overlap_ns(s_start, s_end, w_start, w_end):
    # type: (int, int, int, int) -> int
    """Overlap of a sample interval with the half open window [w_start, w_end)."""
    return max(0, min(s_end, w_end) - max(s_start, w_start))


def cumulative_delta(at_start, at_end, rollover=None):
    # type: (Optional[float], Optional[float], Optional[float]) -> WindowValue
    """
    Difference of a cumulative counter between two boundary observations.

    A decrease is a rollover when the reader declares its rollover value,
    otherwise a reset: the value is then unavailable, never negative.
    """
    if at_start is None or at_end is None:
        return WindowValue(None, None, False, "boundary observation missing")
    if at_end >= at_start:
        return WindowValue(at_end - at_start, 1.0, False, "")
    if rollover:
        return WindowValue(at_end + rollover - at_start, 1.0, False, "rollover")
    return WindowValue(None, None, False, "counter reset")


def series_to_intervals(samples, metric, rollover=None):
    # type: (Iterable[SeriesSample], str, Optional[float]) -> List[SeriesSample]
    """
    Turn a sampled cumulative counter into interval amounts.

    Consecutive samples (start_ns, value) give one interval
    [previous stamp, current stamp) holding the counter difference, so the
    interval rules can clip it. A reset between two samples drops that interval.
    """
    out = []
    prev = None
    for s in sorted(samples, key=lambda x: x.end_ns):
        v = s.values.get(metric)
        if prev is not None and v is not None:
            d = cumulative_delta(prev[1], v, rollover)
            if d.value is not None:
                out.append(SeriesSample(prev[0], s.end_ns, {metric: d.value}))
        if v is not None:
            prev = (s.end_ns, v)
    return out


def interval_count(samples, metric, w_start, w_end):
    # type: (Iterable[SeriesSample], str, int, int) -> WindowValue
    """
    Amount of an interval count metric inside [w_start, w_end).

    A sample straddling a boundary contributes in proportion to its overlap;
    the result is then marked estimated, because an interval total cannot show
    when inside the interval the amount occurred.
    """
    span = w_end - w_start
    if span <= 0:
        return WindowValue(None, None, False, "empty window")
    total, covered, estimated = 0.0, 0, False
    for s in samples:
        v = s.values.get(metric)
        dur = s.end_ns - s.start_ns
        ov = _overlap_ns(s.start_ns, s.end_ns, w_start, w_end)
        if v is None or dur <= 0 or ov <= 0:
            continue
        total += v * ov / dur
        covered += ov
        estimated = estimated or ov < dur
    if covered == 0:
        return WindowValue(None, 0.0, False, "no sample in window")
    reason = "edge interval apportioned" if estimated else ""
    return WindowValue(total, min(covered, span) / span, estimated, reason)


def interval_average(samples, metric, w_start, w_end):
    # type: (Iterable[SeriesSample], str, int, int) -> WindowValue
    """
    Time weighted mean of an interval average metric over [w_start, w_end).

    Coverage reports how much of the window the samples span; the mean is over
    the covered time only, never padded.
    """
    span = w_end - w_start
    if span <= 0:
        return WindowValue(None, None, False, "empty window")
    weighted, covered = 0.0, 0
    for s in samples:
        v = s.values.get(metric)
        ov = _overlap_ns(s.start_ns, s.end_ns, w_start, w_end)
        if v is None or ov <= 0:
            continue
        weighted += v * ov
        covered += ov
    if covered == 0:
        return WindowValue(None, 0.0, False, "no sample in window")
    coverage = min(covered, span) / span
    reason = "" if coverage >= 1.0 else "partial coverage"
    return WindowValue(weighted / covered, coverage, False, reason)


def interval_integral(samples, metric, w_start, w_end, scale=1e-9):
    # type: (Iterable[SeriesSample], str, int, int, float) -> WindowValue
    """
    Integral of an interval average metric over the covered part of the window.

    For power in watts and scale=1e-9 (ns to s) the result is joules. Missing
    time is not filled in: coverage says how much of the window was integrated.
    """
    mean = interval_average(samples, metric, w_start, w_end)
    if mean.value is None:
        return mean
    covered_s = mean.coverage * (w_end - w_start) * scale
    return WindowValue(mean.value * covered_s, mean.coverage, False, mean.reason)


def point_extrema(samples, metric, w_start, w_end):
    # type: (Iterable[SeriesSample], str, int, int) -> Dict[str, WindowValue]
    """
    Observed minimum and maximum of a point metric inside [w_start, w_end).

    Only samples stamped inside the window count: a sample outside cannot
    establish an observed in window extreme.
    """
    vals = [s.values.get(metric) for s in samples
            if w_start <= s.end_ns < w_end and s.values.get(metric) is not None]
    if not vals:
        none = WindowValue(None, None, False, "no observation in window")
        return {"min": none, "max": none, "n": WindowValue(0, None, False, "")}
    return {"min": WindowValue(min(vals), None, False, ""),
            "max": WindowValue(max(vals), None, False, ""),
            "n": WindowValue(len(vals), None, False, "")}


def ratio(numerator, denominator):
    # type: (WindowValue, WindowValue) -> WindowValue
    """
    Ratio of two window totals (for example instructions / cycles = IPC).

    Always total over total on matching coverage, never a mean of interval
    ratios. Unavailable when either side is, or when coverage differs.
    """
    if numerator.value is None or denominator.value in (None, 0):
        return WindowValue(None, None, False, "ratio term unavailable")
    if numerator.coverage != denominator.coverage:
        return WindowValue(None, None, False, "coverage differs between terms")
    est = numerator.estimated or denominator.estimated
    return WindowValue(numerator.value / denominator.value, numerator.coverage, est,
                       "estimated term" if est else "")


__all__ = [
    "CUMULATIVE", "INTERVAL_COUNT", "INTERVAL_AVERAGE", "POINT", "REPRESENTATIONS",
    "SNAPSHOT", "SERIES", "SeriesSample", "WindowValue",
    "cumulative_delta", "series_to_intervals", "interval_count",
    "interval_average", "interval_integral", "point_extrema", "ratio",
]
