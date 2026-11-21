"""
Recorder integration of the idle reference and recording checks.

Uses the real ExperimentRecorder with fake readers and an in-memory store
(e001 + e002 + e003 and a small machine profile), real threads and short
provisional durations. No hardware, no GPU.
"""

import importlib.util
import pathlib
import time

import pytest

import extensions.thermal_exposure.recorder as recmod

# shared stubs, fake writer and migration splitter of the recorder tests
_spec = importlib.util.spec_from_file_location(
    "_recorder_test_base", pathlib.Path(__file__).with_name("test_experiment_recorder.py"))
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)

FAST = {"pre_s": 1.5, "pre_max_s": 2.5, "extend_step_s": 0.5, "final_s": 1.0,
        "post_s": 0.6, "decision_sensor": "soc_zone_0", "gpu_check_s": 0.3}
PROFILE = """
ALTER TABLE thermal_zones ADD COLUMN zone_type TEXT;
ALTER TABLE thermal_zones ADD COLUMN zone_index INTEGER;
ALTER TABLE thermal_zones ADD COLUMN active INTEGER DEFAULT 1;
INSERT OR REPLACE INTO thermal_zones (zone_id, zone_type, zone_index, active) VALUES (1, 'acpitz', 0, 1);
CREATE TABLE IF NOT EXISTS energy_domains (domain_id INTEGER PRIMARY KEY, name TEXT,
    reader_keys TEXT, is_cumulative INTEGER);
INSERT INTO energy_domains VALUES (1, 'PACKAGE', 'package-0,pkg', 1);
CREATE TABLE IF NOT EXISTS idle_baselines (baseline_id TEXT PRIMARY KEY, timestamp REAL);
INSERT INTO idle_baselines VALUES ('base-old', 1.0), ('base-new', 2.0);
INSERT INTO te_sensor (machine_profile, profile_sha256, sensor_name, source_table, zone_type,
    zone_index, gpu_index, canonical_role, unit, nominal_resolution_c, valid_min_c, valid_max_c,
    is_main, loaded_at_utc) VALUES
    ('test', 'x', 'soc_zone_0', 'thermal_samples_v2', 'acpitz', 0, NULL, 'SOC', 'C', 0.001, -10, 125, 0, 'now'),
    ('test', 'x', 'gpu_temp', 'gpu_samples', NULL, NULL, 0, 'GPU', 'C', 1.0, -10, 125, 1, 'now');
"""


def _channel_sql(source_table, key, unit):
    """One main channel row for te_channel."""
    return ("INSERT INTO te_channel (machine_profile, profile_sha256, channel_name, source_table, "
            "source_key, unit, meaning, measurement_kind, channel_role, attribution_eligible, "
            "is_main, loaded_at_utc) VALUES ('test', 'x', 'main', '%s', '%s', '%s', 'test', "
            "'power_reading', 'aggregate', 0, 1, 'now')" % (source_table, key, unit))


@pytest.fixture
def store():
    """In-memory store with e001 + e002 + e003 and a small machine profile."""
    conn = base.sqlite3.connect(":memory:", check_same_thread=False)
    conn.executescript(base.STUBS)
    for f in ("e001_create_thermal_exposure.sql", "e002_recording_between_runs.sql",
              "e003_idle_windows_and_checks.sql", "e004_sensor_resolution_provenance.sql",
              "e005_sensor_samples_and_catalog.sql"):
        for stmt in base._split((base.MIG / f).read_text()):
            conn.execute(stmt)
    conn.executescript(PROFILE)
    conn.commit()
    return conn


def _readers(temp=lambda t: 50.0, energy_calls=None, ambient=False):
    """Fake readers: one zone, GPU temperature, one rail, optional counter and room probe."""
    t0 = time.monotonic()
    out = {
        "THERMAL_ZONE": lambda: [{"zone_id": 1, "temp_celsius": temp(time.monotonic() - t0),
                                  "quality_flag": "VALID"}],
        "GPU_TEMP": lambda: [{"gpu_index": 0, "temperature_c": 50, "source": "test",
                              "quality_flag": "VALID"}],
        "POWER_RAIL": lambda: [{"rail_id": 8, "power_mw": 12000.0, "interval_ns": 50_000_000,
                                "quality_flag": "VALID"}],
    }
    if energy_calls is not None:
        def energy():
            energy_calls.append(time.time_ns())
            return [{"key": "package-0", "energy_uj": (time.monotonic() - t0) * 5e6,
                     "quality_flag": "VALID"}]
        out["ENERGY"] = energy
    if ambient:
        out["AMBIENT"] = lambda: [{"sensor_name": "ambient", "temp_celsius": None,
                                   "quality_flag": "UNAVAILABLE"}]
    return out


def _rec(store, readers, settings, cooldown=None):
    """Recorder with fast rates."""
    rates = {"THERMAL_ZONE": 50.0, "GPU_TEMP": 20.0, "POWER_RAIL": 20.0, "ENERGY": 20.0,
             "AMBIENT": 5.0}
    return recmod.ExperimentRecorder(
        readers=readers, writer_factory=lambda: base.FakeWriter(store),
        rates_hz={k: v for k, v in rates.items() if k in readers},
        pre_window_s=0.5, anchor_s=0.2, flush_s=0.1, cooldown=cooldown,
        offset_fn=lambda: (None, None), settings=settings)


def _one_run(rec, seconds=0.3):
    """Open, one run, close."""
    rec.open()
    rec.pause()
    time.sleep(seconds)
    rec.resume()
    rec.close()


def _checks(store, sid):
    """check name -> result."""
    return dict(store.execute("SELECT check_name, result FROM te_session_check "
                              "WHERE session_id = ?", (sid,)).fetchall())


def _session(store, sid, cols):
    """Selected te_session columns."""
    return store.execute("SELECT %s FROM te_session WHERE session_id = ?" % cols, (sid,)).fetchone()


def test_measured_session_writes_pre_and_post_idle_rows(store):
    store.execute(_channel_sql("power_rail_samples", "8", "mW"))
    rec = _rec(store, _readers(), {"idle": dict(FAST, measure=True), "cooldown": {"hold_s": 0.3}},
               cooldown={"max_s": 3.0})
    _one_run(rec)
    rows = store.execute("SELECT phase, COALESCE(sensor_name, channel_name), median_value, "
                         "mad_value, sample_count FROM te_idle_window WHERE session_id = ?",
                         (rec.session_id,)).fetchall()
    assert {r[0] for r in rows} == {"pre", "post"}
    assert ("pre", "main") in {(r[0], r[1]) for r in rows}
    assert all(r[2] is not None and r[3] is not None and r[4] > 0 for r in rows)
    ref, cooled = _session(store, rec.session_id, "idle_reference, cooling_complete")
    assert (ref, cooled) == ("matched", 1)
    c = _checks(store, rec.session_id)
    assert c["cooling_reached"] == "pass" and c["spans_partition"] == "pass"
    assert c["idle_drift_soc_zone_0"] == "pass"


def test_warm_start_is_flagged(store):
    store.execute(_channel_sql("power_rail_samples", "8", "mW"))
    rec = _rec(store, _readers(temp=lambda t: 60.0 - 1.0 * t), {"idle": dict(FAST, measure=True)},
               cooldown={"max_s": 1.0})
    _one_run(rec)
    assert _session(store, rec.session_id, "idle_equilibrium")[0] == 0
    c = _checks(store, rec.session_id)
    assert c["idle_pre_acceptance"] == "fail" and c["cooling_reached"] == "fail"


def test_room_probe_unavailable_is_a_check_not_zero(store):
    rec = _rec(store, _readers(ambient=True), {})
    _one_run(rec)
    assert _checks(store, rec.session_id)["room_temperature"] == "unknown"
    assert _session(store, rec.session_id, "ambient_status")[0] == "unavailable"


def test_start_after_uncooled_recording_is_carryover_contaminated(store):
    store.execute("INSERT INTO te_session (global_session_id, node_name, profile_id, profile_sha256, "
                  "pacing_plugin, machine_profile, clock_basis, status, start_ns, end_ns, "
                  "cooldown_status) VALUES ('local-1-1', 'local', 'p', 'x', 'none', 'test', 'c', "
                  "'closed', ?, ?, 'timed_out')", (time.time_ns() - 10**10, time.time_ns()))
    rec = _rec(store, _readers(), {})
    _one_run(rec)
    assert _session(store, rec.session_id, "carryover_contaminated")[0] == 1
    assert _checks(store, rec.session_id)["carryover"] == "warn"


def test_not_measured_uses_store_baseline_and_never_reads_counters(store):
    store.execute(_channel_sql("energy_sample_domains", "PACKAGE", "uJ"))
    calls = []
    rec = _rec(store, _readers(energy_calls=calls), {})
    _one_run(rec)
    assert _session(store, rec.session_id, "idle_reference, idle_baseline_id") == \
        ("store_baseline", "base-new")
    assert calls == []
    assert _checks(store, rec.session_id)["idle_pre_acceptance"] == "unknown"


def test_counters_read_only_inside_idle_windows(store):
    store.execute(_channel_sql("energy_sample_domains", "PACKAGE", "uJ"))
    calls = []
    rec = _rec(store, _readers(energy_calls=calls),
               {"idle": dict(FAST, measure=True), "cooldown": {"hold_s": 0.3}},
               cooldown={"max_s": 3.0})
    _one_run(rec, seconds=0.5)
    ws, we = _session(store, rec.session_id, "workload_start_ns, workload_end_ns")
    post = rec.idle.post_start
    rows = store.execute("SELECT timestamp_ns FROM te_energy_samples WHERE session_id = ?",
                         (rec.session_id,)).fetchall()
    assert rows and post is not None
    assert not [t for (t,) in rows if ws <= t < post]       # nothing in run, gap or cool-down
    phases = {r[0] for r in store.execute(
        "SELECT phase FROM te_idle_window WHERE session_id = ? AND method_id = "
        "'idle_power_from_counter_v1'", (rec.session_id,)).fetchall()}
    assert phases == {"pre", "post"}
