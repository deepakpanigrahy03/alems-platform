"""Unit tests for scripts/x1_update_rates.py statistics (synthetic series, no hardware)."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "x1_update_rates", ROOT / "scripts" / "x1_update_rates.py")
x1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(x1)

MS = 1000000  # ns per ms


def hwmon_rows(period_ms, poll_ms, dur_ms, step=4000000):
    """Counter rising by step every period_ms, read every poll_ms (no source time)."""
    return [("counter:pkg", t * MS, None, (t // period_ms) * step)
            for t in range(0, dur_ms, poll_ms)]


def test_pct():
    assert x1.pct([], 50) is None
    assert x1.pct([1, 2, 3, 4], 50) == 2.5
    assert x1.pct([5], 90) == 5


def test_counter_changes_every_100ms():
    st = x1.series_stats(hwmon_rows(100, 5, 2000), use_src=False)
    assert abs(x1.pct(st["change_ms"], 50) - 100) < 1e-6
    assert x1.pct(st["arrival_ms"], 50) == 5
    assert x1.pct(st["steps"], 50) == 4000000
    # every consecutive pair is either a change or a held read
    assert st["same"] + len(st["steps"]) == st["n_arrivals"] - 1


def test_missing_is_not_zero():
    rows = hwmon_rows(100, 5, 200)
    rows[3] = (rows[3][0], rows[3][1], None, None)
    st = x1.series_stats(rows, use_src=False)
    assert st["n_missing"] == 1
    assert st["n_arrivals"] == len(rows) - 1
    assert 0 not in st["steps"]


def test_dcgm_repeated_and_duplicate():
    # source samples every 10 ms, value changes every 100 ms, one duplicated sample
    rows = [("dcgm:156", 0, t * MS, (t // 100) * 1000) for t in range(0, 500, 10)]
    rows.insert(5, rows[4])
    st = x1.series_stats(rows, use_src=True)
    assert st["n_dup_ts"] == 1
    assert abs(x1.pct(st["change_ms"], 50) - 100) < 1e-6
    assert st["same"] > 0


def test_missing_source_timestamp():
    rows = [("dcgm:156", 0, None, 5), ("dcgm:156", 0, 10 * MS, 5)]
    st = x1.series_stats(rows, use_src=True)
    assert st["n_missing_ts"] == 1
    assert st["n_arrivals"] == 1


def test_held_vs_repeated_columns():
    st = x1.series_stats(hwmon_rows(100, 5, 300), use_src=False)
    row = x1.summary_row({"channel": "counter:pkg"}, st, use_src=False, gap_ms=300)
    assert row["held"] is not None and row["repeated"] is None
    row = x1.summary_row({"channel": "dcgm:156"}, st, use_src=True, gap_ms=300)
    assert row["held"] is None and row["repeated"] is not None


def test_gaps_counted():
    rows = hwmon_rows(100, 100, 1000)
    rows = rows[:3] + rows[7:]  # 400 ms hole
    st = x1.series_stats(rows, use_src=False)
    row = x1.summary_row({}, st, use_src=False, gap_ms=300)
    assert row["n_gaps_over_threshold"] == 1


def test_merge_pools_lists_and_counts():
    a = x1.series_stats(hwmon_rows(100, 5, 500), use_src=False)
    m = x1.merge_stats([a, a])
    assert m["n_reads"] == 2 * a["n_reads"]
    assert len(m["change_ms"]) == 2 * len(a["change_ms"])


def test_channel_note_unresolved_and_constant():
    cfg = {"hwmon_poll_ms": 5, "hwmon_unresolved_factor": 1.5,
           "dcgm": {"resolution_note_ms": 100}}
    fast = x1.series_stats([("rail:gpu", t * MS, None, t) for t in range(0, 100, 5)], False)
    assert "not resolvable" in x1.channel_note(cfg, "rail:gpu", fast)
    flat = x1.series_stats([("rail:dla", t * MS, None, 7) for t in range(0, 100, 5)], False)
    assert x1.channel_note(cfg, "rail:dla", flat) == "no value change in step"


def test_channel_note_resolved_counter():
    cfg = {"hwmon_poll_ms": 5, "hwmon_unresolved_factor": 1.5,
           "dcgm": {"resolution_note_ms": 100}}
    st = x1.series_stats(hwmon_rows(100, 5, 1000), use_src=False)
    assert x1.channel_note(cfg, "counter:pkg", st) is None
