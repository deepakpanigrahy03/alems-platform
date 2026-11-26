"""Window contract rules (alems_sdk.window): allocation, conservation, half open windows."""
from alems_sdk.window import (
    SeriesSample, cumulative_delta, series_to_intervals, interval_count,
    interval_average, interval_integral, point_extrema, ratio,
)

MS = 1_000_000


def _s(a_ms, b_ms, **values):
    return SeriesSample(a_ms * MS, b_ms * MS, values)


def test_short_task_inside_one_interval_is_estimated():
    # 20 ms task inside a 100 ms interval holding 1,000 instructions -> 200, estimated.
    w = interval_count([_s(0, 100, inst=1000)], "inst", 40 * MS, 60 * MS)
    assert w.value == 200 and w.estimated and w.coverage == 1.0


def test_adjacent_windows_conserve():
    samples = [_s(0, 100, n=1000), _s(100, 200, n=500)]
    pre = interval_count(samples, "n", 0, 70 * MS).value
    task = interval_count(samples, "n", 70 * MS, 130 * MS).value
    post = interval_count(samples, "n", 130 * MS, 200 * MS).value
    assert abs(pre + task + post - 1500) < 1e-9


def test_point_at_end_belongs_to_next_window():
    samples = [_s(100, 100, t=50.0)]
    assert point_extrema(samples, "t", 0, 100 * MS)["max"].value is None
    assert point_extrema(samples, "t", 100 * MS, 200 * MS)["max"].value == 50.0


def test_cumulative_rollover_and_reset():
    assert cumulative_delta(90, 10, rollover=100).value == 20
    assert cumulative_delta(90, 10).value is None
    assert cumulative_delta(None, 10).value is None


def test_sampled_counter_becomes_intervals_and_clips():
    samples = [_s(0, 0, e=1000), _s(100, 100, e=1400), _s(200, 200, e=1600)]
    iv = series_to_intervals(samples, "e")
    total = interval_count(iv, "e", 0, 200 * MS)
    assert total.value == 600 and not total.estimated


def test_power_integral_reports_coverage():
    samples = [_s(0, 500, p=40.0)]                      # 40 W over the first half second
    j = interval_integral(samples, "p", 0, 1000 * MS)   # one second window
    assert abs(j.value - 20.0) < 1e-9 and j.coverage == 0.5


def test_mean_not_padded():
    w = interval_average([_s(0, 500, f=2000.0)], "f", 0, 1000 * MS)
    assert w.value == 2000.0 and w.coverage == 0.5


def test_ipc_is_total_over_total():
    inst = interval_count([_s(0, 100, i=300), _s(100, 200, i=100)], "i", 0, 200 * MS)
    cyc = interval_count([_s(0, 100, c=100), _s(100, 200, c=300)], "c", 0, 200 * MS)
    assert ratio(inst, cyc).value == 1.0              # mean of interval ratios would be 1.67


def test_unknown_is_none_never_zero():
    assert interval_count([], "x", 0, MS).value is None
    assert interval_average([_s(0, 1, x=None)], "x", 0, MS).value is None
