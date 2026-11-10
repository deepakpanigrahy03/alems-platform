"""
tests/test_dcgm_readiness_coverage.py

Regression tests for GPU energy sampling coverage (DCGM on GB10):
  * readiness: sampling waits for a first valid reading, which becomes the
    baseline of the first delta (the first interval is no longer lost);
  * timeout: no valid reading gives NULL coverage with a reason, never 0;
  * no cap: the sample queue is unbounded (runs over 200 s kept whole);
  * coverage facts: rows, interior gaps above 300 ms, max gap;
  * shared window coverage of the run window.
"""

import core.readers.gpu_collector as gc
from core.readers.gpu_collector import GPUCollector, GpuSample, coverage_for_run

SEC = 1000000000
MS = 1000000
T0 = 1800000000 * SEC          # arbitrary wall clock epoch ns


class _Backend(object):
    """Backend stub returning a scripted sequence of energy readings."""

    SOURCE = "dcgm"

    def __init__(self, values):
        self._values = list(values)
        self.calls = 0

    def is_available(self):
        """Always available, like a connected DCGM daemon."""
        return True

    def read_energy_uj(self, gpu_index=0):
        """Return the next scripted value; the last one repeats."""
        self.calls += 1
        if len(self._values) > 1:
            return self._values.pop(0)
        return self._values[0]

    def read_signals(self, gpu_index=0):
        """No signals needed for these tests."""
        return {}


def _collector(monkeypatch, values):
    """GPUCollector wired to the stub backend (no hardware, no policy file)."""
    monkeypatch.setattr(GPUCollector, "_detect_backend",
                        lambda self, rapl_reader: _Backend(values))
    return GPUCollector(rapl_reader=None, sampling_hz=10)


def _sample(start_ns, end_ns):
    """One GPU sample covering [start_ns, end_ns]."""
    return GpuSample(gpu_index=0, sample_start_ns=start_ns, sample_end_ns=end_ns,
                     interval_ns=end_ns - start_ns, energy_start_uj=0,
                     energy_end_uj=1, energy_uj=1, power_mw=None,
                     util_gpu_pct=None, util_mem_pct=None, sm_clock_mhz=None,
                     mem_clock_mhz=None, mem_used_mb=None, temperature_c=None,
                     source="dcgm")


def test_readiness_blocks_until_first_valid_reading(monkeypatch):
    """Two blank readings, then a valid one: ready, and it is the baseline."""
    col = _collector(monkeypatch, [None, None, 5000])
    col._wait_ready()
    assert col._ready_status == "ready"
    assert col.backend.calls == 3
    # The valid reading is the delta baseline, so the first interval is kept.
    assert col._prev_energy_uj == 5000
    assert col._prev_ts_ns is not None


def test_readiness_timeout_gives_null_coverage_with_reason(monkeypatch):
    """No valid reading within the timeout: status timeout, coverage NULL."""
    monkeypatch.setattr(gc, "READY_TIMEOUT_S", 0.2)
    col = _collector(monkeypatch, [None])
    col._wait_ready()
    assert col._ready_status == "timeout"
    assert col._prev_energy_uj is None
    cov = col._coverage_stats([])
    share, reason = coverage_for_run(cov, T0, T0 + 10 * SEC)
    assert share is None
    assert reason == "readiness_timeout"


def test_queue_is_unbounded(monkeypatch):
    """More than 2,000 samples are all kept (the old cap dropped the oldest)."""
    col = _collector(monkeypatch, [1000])
    assert col._queue.maxsize == 0
    for i in range(2500):
        col._queue.put_nowait(i)
    assert col._queue.qsize() == 2500


def test_coverage_stats_counts_rows_and_interior_gaps(monkeypatch):
    """100 ms cadence with one 400 ms hole: one interior gap, max 400 ms."""
    col = _collector(monkeypatch, [1000])
    col._wait_ready()
    ends = [T0 + k * 100 * MS for k in range(1, 11)]   # 10 rows at 100 ms
    ends.append(ends[-1] + 400 * MS)                     # one late row
    samples = [_sample(e - 100 * MS, e) for e in ends]
    cov = col._coverage_stats(samples)
    assert cov["row_count"] == 11
    assert cov["interior_gaps_gt_300ms"] == 1
    assert abs(cov["max_gap_ms"] - 400.0) < 1e-6
    assert cov["first_valid_utc_ns"] == T0
    assert cov["last_valid_utc_ns"] == ends[-1]
    assert cov["cap_detected"] is False


def test_stop_takes_final_reading(monkeypatch):
    """stop() keeps the energy since the last tick (final reading after join)."""
    col = _collector(monkeypatch, [1500])
    col._prev_energy_uj = 1000
    col._prev_ts_ns = T0
    col._thread = None               # loop already ended; stop() must still read once
    samples = col.stop()
    assert len(samples) == 1
    assert samples[0].energy_uj == 500
    assert samples[0].sample_start_ns == T0
    assert col.last_coverage["row_count"] == 1


def test_stop_final_reading_skipped_without_baseline(monkeypatch):
    """No valid baseline (readiness timed out): no fabricated final sample."""
    col = _collector(monkeypatch, [1500])
    col._prev_energy_uj = None
    col._prev_ts_ns = None
    col._thread = None
    assert col.stop() == []


def test_shared_window_coverage(monkeypatch):
    """Samples cover 9.9 s of a 10 s window: coverage 0.99."""
    cov = {"ready_status": "ready", "first_valid_utc_ns": T0 + 100 * MS,
           "last_valid_utc_ns": T0 + 10 * SEC}
    share, reason = coverage_for_run(cov, T0, T0 + 10 * SEC)
    assert reason is None
    assert abs(share - 0.99) < 1e-9


def test_shared_window_coverage_unknown_window_is_null():
    """Missing run window gives NULL with a reason, never 0 (MIC-1)."""
    cov = {"ready_status": "ready", "first_valid_utc_ns": T0,
           "last_valid_utc_ns": T0 + SEC}
    share, reason = coverage_for_run(cov, None, None)
    assert share is None
    assert reason == "run_window_unknown"
