"""
alems_plugin_pravaha.window: bytes and steadiness of pravaha traffic samples over a work window.

Author: Deepak Panigrahy

Shared by the copy (copy_bytes) and kernel (kernel_read_miss_bytes, kernel_write_bytes)
benchmark steps. Samples are dicts with t_start_ns, t_end_ns and byte columns; a sample
straddling a window edge counts in proportion to its overlap. A missing value is skipped,
never read as 0.
"""

from typing import Any, Dict, List, Optional


def bytes_in(samples, a, b, column):
    # type: (List[Dict[str, Any]], int, int, str) -> Optional[float]
    """Bytes of `column` over [a, b], edge samples prorated; None when no sample has a value."""
    total, any_value = 0.0, False
    for s in samples:
        s0, s1, v = s.get("t_start_ns"), s.get("t_end_ns"), s.get(column)
        if s0 is None or s1 is None or v is None or s1 <= s0:
            continue
        overlap = min(s1, b) - max(s0, a)
        if overlap > 0:
            total += v * overlap / (s1 - s0)
            any_value = True
    return total if any_value else None


def steadiness(samples, t0, t1, column, bin_s, tol, coverage):
    # type: (List[Dict[str, Any]], int, int, str, float, float, float) -> str
    """
    steady, not_steady or unknown for `column`, from bins of bin_s anchored at t0 (a final
    bin under half length is dropped). unknown when a bin is under `coverage` covered or the
    mean rate is not positive (no division by a zero mean).
    """
    bin_ns = int(bin_s * 1e9)
    rates = []
    for start in range(t0, t1, bin_ns):
        end = min(start + bin_ns, t1)
        if end - start < bin_ns / 2:
            break
        covered = sum(max(0, min(s["t_end_ns"], end) - max(s["t_start_ns"], start)) for s in samples
                      if s.get("t_start_ns") is not None and s.get("t_end_ns") is not None)
        if covered < coverage * (end - start):
            return "unknown"
        rates.append((bytes_in(samples, start, end, column) or 0.0) / ((end - start) / 1e9))
    if not rates:
        return "unknown"
    mean = sum(rates) / len(rates)
    if mean <= 0:
        return "unknown"
    return "steady" if all(abs(r - mean) <= tol * mean for r in rates) else "not_steady"


def measured_samples(run):
    # type: (Dict[str, Any]) -> List[Dict[str, Any]]
    """Samples of every traffic reader of the run whose status is measured."""
    return [s for r in (run.get("traffic") or []) if r.get("status") == "measured"
            for s in r.get("samples") or []]
