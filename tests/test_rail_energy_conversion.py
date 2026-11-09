"""
tests/test_rail_energy_conversion.py

Regression test for rail energy conversion in EnergyCollector._tick.

Power rails (domain_type POWER_RAIL) report instantaneous power in mW.
The collector must store energy per tick in uJ using the MEASURED tick
interval (tick_end_ns minus previous tick), never the planned sampling
interval: E_uJ = P_mW * interval_ns / 1e6.

A regression here stores power as energy, which the agreement validator
reports as AGREE-RAIL FAIL ("power (mW) stored as energy (uJ)").
"""

from types import SimpleNamespace
from typing import List

import core.readers.energy_collector as ec_mod
from core.readers.energy_collector import EnergyCollector
from core.readers.measurement_schema import ENERGY_COUNTER, POWER_RAIL

# Synthetic inputs from the test definition: 50,000 mW over 100 ms.
RAIL_MW = 50000
INTERVAL_100MS_NS = 100000000
# A tick that ran late: planned 100 ms (10 Hz) but measured 150 ms.
INTERVAL_150MS_NS = 150000000
START_NS = 1000000000000


class _CaptureAdapter(object):
    """Adapter stub that keeps every sample written by the collector."""

    def __init__(self):
        self.samples = []  # type: List

    def write(self, sample):
        """Store the sample for inspection."""
        self.samples.append(sample)


class _FixedReader(object):
    """Reader stub returning one fixed native key dict per call."""

    def __init__(self, reading):
        self._reading = reading

    def read_energy(self):
        """Return the fixed reading (native keys, rail values in mW)."""
        return dict(self._reading)


def _collector(reading, domain_map, monkeypatch, tick_end_ns):
    """
    Build a collector wired for one direct _tick() call.

    __init__ is bypassed on purpose: it calls the reader schema and the
    sampling policy, which are not under test. Only the state that _tick
    reads is set, so the test exercises the conversion line alone.

    Args:
        reading: native key dict returned by the reader.
        domain_map: native_key -> descriptor stub (domain_type, canonical_name).
        monkeypatch: pytest fixture used to freeze the tick clock.
        tick_end_ns: value time.time_ns() returns at the tick.

    Returns:
        (collector, adapter) ready for collector._tick().
    """
    col = EnergyCollector.__new__(EnergyCollector)
    adapter = _CaptureAdapter()
    col._reader = _FixedReader(reading)
    col._adapters = [adapter]
    col._run_id = 1
    col._schema = SimpleNamespace(source="TEST")
    col._domain_map = domain_map
    col._wrap_max = 1 << 32
    col._prev = dict(reading)
    # Previous tick fixed so the measured interval is exactly controlled.
    col._prev_tick_ns = START_NS
    col._stats = {"ticks": 0, "emitted": 0, "empty": 0, "errors": 0,
                  "max_gap_ns": 0, "late_join": False}
    # Freeze the clock inside the collector module only.
    monkeypatch.setattr(ec_mod, "time", SimpleNamespace(time_ns=lambda: tick_end_ns))
    return col, adapter


def _rail(canonical_name):
    """Descriptor stub for a power rail domain."""
    return SimpleNamespace(domain_type=POWER_RAIL, canonical_name=canonical_name)


def test_rail_50000_mw_over_100_ms_is_5000000_uj(monkeypatch):
    """50,000 mW over a measured 100 ms gives 5,000,000 uJ (5 J)."""
    col, adapter = _collector({"soc_pkg": RAIL_MW}, {"soc_pkg": _rail("SOC_PKG")},
                              monkeypatch, START_NS + INTERVAL_100MS_NS)
    assert col._tick() is True
    assert len(adapter.samples) == 1
    sample = adapter.samples[0]
    assert sample.get_domain("SOC_PKG") == 5000000
    # The stored interval is the measured one.
    assert sample.interval_ns == INTERVAL_100MS_NS


def test_rail_uses_measured_not_planned_interval(monkeypatch):
    """A late tick (150 ms measured, 100 ms planned) integrates 150 ms."""
    col, adapter = _collector({"soc_pkg": RAIL_MW}, {"soc_pkg": _rail("SOC_PKG")},
                              monkeypatch, START_NS + INTERVAL_150MS_NS)
    assert col._tick() is True
    # 50,000 mW x 0.150 s = 7.5 J; the planned 100 ms would give 5.0 J.
    assert adapter.samples[0].get_domain("SOC_PKG") == 7500000


def test_all_six_rail_domains_converted(monkeypatch):
    """Every rail domain checked by AGREE-RAIL is stored as uJ, not mW."""
    keys = {"soc_pkg": "SOC_PKG", "cpu_gpu": "CPU_GPU", "vcore": "VCORE",
            "dc_input": "DC_INPUT", "prereg": "PREREG", "dla": "DLA"}
    reading = dict((k, RAIL_MW) for k in keys)
    domain_map = dict((k, _rail(v)) for k, v in keys.items())
    col, adapter = _collector(reading, domain_map, monkeypatch,
                              START_NS + INTERVAL_100MS_NS)
    assert col._tick() is True
    for name in keys.values():
        assert adapter.samples[0].get_domain(name) == 5000000, name


def test_missing_rail_is_absent_not_zero(monkeypatch):
    """A rail reading of None is left out of the sample (MIC-1), never 0."""
    domain_map = {"soc_pkg": _rail("SOC_PKG"), "vcore": _rail("VCORE")}
    col, adapter = _collector({"soc_pkg": RAIL_MW, "vcore": None}, domain_map,
                              monkeypatch, START_NS + INTERVAL_100MS_NS)
    assert col._tick() is True
    sample = adapter.samples[0]
    assert sample.has_domain("SOC_PKG")
    assert not sample.has_domain("VCORE")


def test_counter_domain_unchanged_by_rail_rule(monkeypatch):
    """Energy counters stay plain deltas; the rail rule never applies to them."""
    counter = SimpleNamespace(domain_type=ENERGY_COUNTER, canonical_name="PACKAGE")
    col, adapter = _collector({"pkg": 2000}, {"pkg": counter},
                              monkeypatch, START_NS + INTERVAL_100MS_NS)
    col._prev = {"pkg": 1500}  # delta 500 uJ regardless of interval
    assert col._tick() is True
    assert adapter.samples[0].get_domain("PACKAGE") == 500
