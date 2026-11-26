"""Window contract wiring: engine window values, CPU ticks snapshot, perf interval clipping."""
from types import SimpleNamespace

from core.energy_engine import EnergyEngine
from core.readers.perf_reader import PerfReader
from core.utils.proc_reader import CPUTicksSnapshot

MS = 1_000_000


def _fake_engine(values_by_mark, metrics):
    reader = SimpleNamespace(METRICS=metrics, ROLLOVER={})
    bounds = {mark: {"snapshots": {"r": {"values": vals}}} for mark, vals in values_by_mark.items()}
    return SimpleNamespace(_bounds=bounds, r=reader)


def test_window_values_cumulative_and_point():
    eng = _fake_engine({"before": {"c": 0, "p": 1}, "t0": {"c": 10, "p": 2},
                        "t1": {"c": 40, "p": 3}, "after": {"c": 45, "p": 4}},
                       {"c": "cumulative", "p": "point"})
    w = EnergyEngine.window_values(eng, "r")
    assert (w["c"]["pre"].value, w["c"]["task"].value, w["c"]["post"].value) == (10, 30, 5)
    assert (w["p"]["at_t0"], w["p"]["at_t1"]) == (2, 3)


def test_failed_snapshot_gives_none_not_zero():
    eng = _fake_engine({"t0": {"c": 10}, "t1": None}, {"c": "cumulative"})
    assert EnergyEngine.window_values(eng, "r")["c"]["task"].value is None


def test_cpu_ticks_snapshot_declares_contract():
    snap = CPUTicksSnapshot(0).snapshot()
    assert CPUTicksSnapshot.WINDOW_KIND == "snapshot"
    assert snap is None or (snap["process_ticks"] is None and snap["total_ticks"] is not None)


def _perf(window=None):
    p = object.__new__(PerfReader)
    p._perf_start_wall_ns = 0
    p._window = window
    return p


def test_perf_interval_clip_short_task_is_proportional():
    out = "0.100,1000,,instructions,,,\n0.200,500,,instructions,,,\n"
    # 20 ms task inside the first 100 ms interval: 200 of its 1,000 instructions
    assert _perf((40 * MS, 60 * MS))._interval_counters(out) == {"instructions": 200}


def test_perf_interval_without_window_sums_all():
    out = "#  time counts unit events\n0.100,1000,,instructions,,,\n0.200,500,,instructions,,,\n0.200,<not counted>,,cycles,,,\n"
    assert _perf()._interval_counters(out) == {"instructions": 1500}
