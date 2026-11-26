"""Capability registry and store audit (45.5b, 45.5c): synthetic store, no GPU."""
import sqlite3
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import experiment_capabilities as ec  # noqa: E402


def make_store(path, schema=121):
    c = sqlite3.connect(str(path))
    c.executescript("""
    CREATE TABLE schema_version (version INTEGER);
    CREATE TABLE experiments (exp_id INTEGER, name TEXT, model_name TEXT);
    CREATE TABLE runs (run_id INTEGER, exp_id INTEGER, start_time_ns INTEGER, end_time_ns INTEGER,
                       completion_tokens INTEGER, ttft_ms REAL, max_temp_c REAL);
    CREATE TABLE energy_samples_v2 (sample_id INTEGER, run_id INTEGER, timestamp_ns INTEGER, interval_ns INTEGER);
    CREATE TABLE energy_sample_domains (sample_id INTEGER, domain_id INTEGER, energy_uj INTEGER);
    CREATE TABLE gpu_samples (run_id INTEGER, sample_start_ns INTEGER, sample_end_ns INTEGER, energy_uj INTEGER);
    CREATE TABLE orchestration_events (run_id INTEGER, phase TEXT, event_type TEXT, tool_success INTEGER);
    CREATE TABLE serving_runtime_snapshots (snapshot_id INTEGER, kv_occupancy_fraction REAL);
    """)
    c.execute("INSERT INTO schema_version VALUES (?)", (schema,))
    c.executemany("INSERT INTO experiments VALUES (?,?,?)", [(1, "agentic", "m"), (2, "linear", "m")])
    c.executemany("INSERT INTO runs VALUES (?,?,?,?,?,?,?)",
                  [(10, 1, 0, 1000, 5, 1.0, 60.0), (11, 1, 0, 1000, 5, None, 60.0), (20, 2, 0, 1000, None, None, None)])
    sid = 0
    for rid, doms in ((10, (1, 7)), (11, (1, 7))):
        for d in doms:
            sid += 1
            c.execute("INSERT INTO energy_samples_v2 VALUES (?,?,?,?)", (sid, rid, 100, 100))
            c.execute("INSERT INTO energy_sample_domains VALUES (?,?,?)", (sid, d, 5))
    c.executemany("INSERT INTO gpu_samples VALUES (?,?,?,?)", [(10, 0, 500, 1), (10, 500, 1000, 1), (11, 600, 900, 1)])
    c.executemany("INSERT INTO orchestration_events VALUES (?,?,?,?)",
                  [(10, "execution", "tool_call", 1), (11, "execution", "tool_call", None), (11, "planning", "llm_call", None)])
    c.commit()
    c.close()


def cfg_for(path, min_schema=None):
    cfg = yaml.safe_load(open(ROOT / "config" / "experiment_capabilities.yaml"))
    cfg["stores"] = {"test": str(path)}
    cfg["phase_boundary_min_schema"] = min_schema
    cfg["min_run_share"] = 0.95
    cfg["audit"]["dcgm_cap_rows"] = 2
    return cfg


def rows(tmp_path, min_schema=None, schema=121):
    db = tmp_path / ("s%s.db" % min_schema)
    make_store(db, schema)
    exp, store, run = ec.store_rows("test", str(db), cfg_for(db, min_schema))
    return {r["exp_id"]: r for r in exp}, store, {r["run_id"]: r for r in run}


def test_domain_and_table_capabilities(tmp_path):
    exp, _, run = rows(tmp_path)
    assert exp[1]["spbm_package"] == "yes" and exp[1]["spbm_gpu"] == "yes"
    assert exp[1]["spbm_cpu"] == "no" and exp[2]["spbm_package"] == "no"
    assert exp[1]["tool_events"] == "yes"
    assert exp[1]["tool_outcome"] == "no" and exp[1]["share_tool_outcome"] == 0.5
    assert run[20]["energy_samples"] == "no"


def test_table_without_run_id_is_unknown(tmp_path):
    exp, _, _ = rows(tmp_path)
    assert exp[1]["serving_snapshots"] == "unknown" and exp[1]["share_serving_snapshots"] is None


def test_missing_table_is_no(tmp_path):
    exp, _, _ = rows(tmp_path)
    assert exp[1]["sequencer_link"] == "no"


def test_phase_trust_unknown_until_configured(tmp_path):
    exp, _, _ = rows(tmp_path, min_schema=None)
    assert exp[1]["phase_boundaries_trusted"] == "unknown"
    assert exp[1]["usable_for_ownership_bounds_phases"] == "unknown"
    exp, _, _ = rows(tmp_path, min_schema=122)
    assert exp[1]["phase_boundaries_trusted"] == "no"
    exp, _, _ = rows(tmp_path, min_schema=121)
    assert exp[1]["phase_boundaries_trusted"] == "yes"


def test_usable_for_and_blocking(tmp_path):
    exp, _, _ = rows(tmp_path)
    assert exp[1]["usable_for_ownership_bounds_tool_windows"] == "yes"
    assert exp[1]["usable_for_gpu_equivalent_goals"] == "no"
    assert "sequencer_link" in exp[1]["blocking_gpu_equivalent_goals"]
    assert exp[1]["ttft"] == "no" and exp[1]["share_ttft"] == 0.5


def test_dcgm_audit(tmp_path):
    exp, store, run = rows(tmp_path)
    assert run[10]["dcgm_capped"] == 1 and run[10]["dcgm_cover"] == 1.0
    assert run[11]["dcgm_capped"] == 0 and run[11]["dcgm_cover"] == 0.3
    assert run[20]["dcgm_rows"] is None
    assert exp[1]["n_dcgm_cover_low"] == 1 and store["n_dcgm_capped"] == 1


def test_config_rejects_unknown_capability(tmp_path):
    bad = yaml.safe_load(open(ROOT / "config" / "experiment_capabilities.yaml"))
    bad["usable_for"]["x"] = ["no_such"]
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(bad))
    with pytest.raises(SystemExit):
        ec.load_config(p)


def test_main_writes_csv(tmp_path):
    db = tmp_path / "s.db"
    make_store(db)
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(cfg_for(db)))
    assert ec.main(["--out-dir", str(tmp_path), "--config", str(p), "--per-run"]) == 0
    out = next(tmp_path.glob("capabilities_*"))
    assert {f.name for f in out.iterdir()} == {"capabilities_experiments.csv", "capabilities_stores.csv",
                                               "capabilities_runs.csv"}
