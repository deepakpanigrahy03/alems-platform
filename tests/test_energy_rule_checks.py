"""
tests/test_energy_rule_checks.py

Three kinds of energy chain checks (expected containment, observed ordering,
additivity residual), the sys_total vs dc_input conflict, N/A rules, and the
label cleanup (no 1 Hz DCGM claim, no NVLink wording, heuristic confidence).
"""

import pathlib
import sqlite3
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import agreement_validator as av          # noqa: E402
import energy_rule_checks as rc           # noqa: E402

DOMAINS = {1: 'PACKAGE', 5: 'CPU_P', 6: 'CPU_E', 7: 'GPU', 20: 'DLA',
           25: 'SOC_PKG', 26: 'CPU_GPU', 27: 'VCORE', 28: 'DC_INPUT', 29: 'PREREG'}
NAME_ID = dict((v, k) for k, v in DOMAINS.items())
TICK_NS = 100000000          # 100 ms


def _db():
    """In memory store with the tables the checks read."""
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE energy_domains (domain_id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE energy_samples_v2 (sample_id INTEGER PRIMARY KEY, run_id INTEGER,
            source_id INTEGER, timestamp_ns INTEGER, interval_ns INTEGER);
        CREATE TABLE energy_sample_domains (sample_id INTEGER, run_id INTEGER,
            domain_id INTEGER, source_id INTEGER, energy_uj INTEGER);
        CREATE TABLE power_rails (rail_id INTEGER PRIMARY KEY, rail_name TEXT);
        CREATE TABLE power_rail_samples (run_id INTEGER, rail_id INTEGER,
            timestamp_ns INTEGER, power_mw INTEGER, interval_ns INTEGER);
    """)
    c.executemany("INSERT INTO energy_domains VALUES (?, ?)", list(DOMAINS.items()))
    return c


def _fill(c, n_ticks, watts, run_id=1, override=None):
    """n_ticks of 100 ms, each domain at a constant power (W); override(i, w) edits a tick."""
    for i in range(n_ticks):
        sid = run_id * 100000 + i
        c.execute("INSERT INTO energy_samples_v2 VALUES (?, ?, 2, ?, ?)",
                  (sid, run_id, i * TICK_NS, TICK_NS))
        w = dict(watts) if override is None else override(i, dict(watts))
        for name, p in w.items():
            if p is not None:
                c.execute("INSERT INTO energy_sample_domains VALUES (?, ?, ?, 2, ?)",
                          (sid, run_id, NAME_ID[name], int(p * 1e5)))   # W x 0.1 s = uJ/1e6


NORMAL = {'DC_INPUT': 60.0, 'SOC_PKG': 42.0, 'PACKAGE': 42.0, 'CPU_GPU': 30.0,
          'GPU': 24.0, 'CPU_P': 4.0, 'CPU_E': 1.0, 'DLA': 0.05,
          'VCORE': 9.0, 'PREREG': 8.0}


def test_containment_holds_on_normal_run():
    c = _db(); _fill(c, 200, NORMAL)
    out = rc.validate_rules(c, 1, 'arm_spbm')
    for key in ('cpu_gpu_in_soc_pkg', 'gpu_in_cpu_gpu', 'cpu_p_in_cpu_gpu', 'cpu_e_in_cpu_gpu'):
        assert out[key]['status'] == 'OK', key
        assert out[key]['kind'] == 'expected_containment'
        assert out[key]['violating_share'] == 0.0
    assert abs(out['gpu_in_cpu_gpu']['diff_w_p50'] - 6.0) < 1e-6   # 30 W - 24 W


def test_containment_violation_share_and_warn():
    def bump(i, w):
        if i % 10 == 0:                   # 10 % of ticks: CPU_GPU above SOC_PKG
            w['CPU_GPU'] = 45.0
        return w
    c = _db(); _fill(c, 200, NORMAL, override=bump)
    r = rc.validate_rules(c, 1, 'arm_spbm')['cpu_gpu_in_soc_pkg']
    assert r['status'] == 'WARN'
    assert abs(r['violating_share'] - 0.1) < 1e-9
    assert r['diff_w_p05'] < 0 < r['diff_w_p95']


def test_ordering_is_labelled_observed_and_never_fails():
    c = _db(); _fill(c, 200, dict(NORMAL, DC_INPUT=40.0))   # package above dc_input
    r = rc.validate_rules(c, 1, 'arm_spbm')['package_le_dc_input']
    assert r['kind'] == 'observed_ordering'
    assert r['status'] == 'WARN'
    assert r['status'] != 'FAIL'


def test_additivity_residuals():
    c = _db(); _fill(c, 200, NORMAL)
    out = rc.validate_rules(c, 1, 'arm_spbm')
    a = out['cpu_gpu_additivity']
    assert a['kind'] == 'additivity_residual' and a['status'] == 'OK'
    assert abs(a['residual_share'] - 1.0 / 30.0) < 1e-3       # 30 - (24 + 4 + 1)
    p = out['package_additivity']
    assert p['children'] == 'CPU_P + CPU_E + GPU + DLA'
    assert p['status'] == 'OK'


def test_vcore_and_prereg_never_summed():
    for _, parent, children in rc.ADDITIVE:
        assert not set((parent,) + tuple(children)) & set(rc.NEVER_SUMMED)
    r = rc._additive([], 'CPU_GPU', ('VCORE',))
    assert r['status'] == 'N/A' and 'never' in r['reason']


def test_short_window_is_na_with_reason():
    c = _db(); _fill(c, 50, NORMAL)               # 5 s
    r = rc.validate_rules(c, 1, 'arm_spbm')['gpu_in_cpu_gpu']
    assert r['status'] == 'N/A' and 'shorter than 10 s' in r['reason']


def test_missing_samples_is_na_with_reason():
    def drop(i, w):
        if i % 20 == 0:                   # 5 % of ticks lack GPU
            w['GPU'] = None
        return w
    c = _db(); _fill(c, 200, NORMAL, override=drop)
    r = rc.validate_rules(c, 1, 'arm_spbm')['gpu_in_cpu_gpu']
    assert r['status'] == 'N/A' and 'missing samples' in r['reason']


def test_non_gb10_platform_returns_nothing():
    assert rc.validate_rules(_db(), 1, 'x86_rapl') == {}


def test_top_pair_conflict_warns_with_distribution_never_fails():
    c = _db()
    c.executemany("INSERT INTO power_rails VALUES (?, ?)", [(1, 'sys_total'), (2, 'dc_input')])
    for i in range(100):
        t = i * TICK_NS
        c.execute("INSERT INTO power_rail_samples VALUES (1, 1, ?, 60900, ?)", (t, TICK_NS))
        c.execute("INSERT INTO power_rail_samples VALUES (1, 2, ?, 60000, ?)", (t, TICK_NS))
    r = av._top_pair(c, 1)
    assert r['status'] == 'WARN' and r['kind'] == 'conflict'
    assert abs(r['gap_w_p50'] - 0.9) < 1e-6


def test_labels_cleaned():
    """No 1 Hz DCGM claim, no NVLink attribution, no c2c token, heuristic confidence."""
    report = (SCRIPTS / "report_energy_chain.py").read_text()
    config = (SCRIPTS / "platform_config.py").read_text()
    assert "1 Hz" not in report and "c2c=" not in report
    assert "NVLink" not in report and "nvlink_c2c" not in report
    assert "'rate_hz':   1," not in config and "NVLink" not in config
    assert "DCGM field 156 (update rate measured in X1)" in report
    assert "heuristic confidence" in report
