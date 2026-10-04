"""Unit tests for WP 2c stage graphs and recorder (no hardware, no store)."""
import os
import sqlite3

import pytest

from core.observability import stage_graphs as sg
from core.observability import stages as st

def _repo_root():
    """Walk up from this file to the directory holding migrations/."""
    d = os.path.dirname(os.path.abspath(__file__))
    while not os.path.isdir(os.path.join(d, "migrations")):
        parent = os.path.dirname(d)
        if parent == d:
            raise RuntimeError("repo root with migrations/ not found")
        d = parent
    return d


MIG = os.path.join(_repo_root(), "migrations", "schema", "v120_stage_event.sql")

DESIGN_15_3 = {
    "save_pair": {"persist_run", "persist_samples", "spans", "attribution", "residual",
                  "quality", "etl_phase", "etl_hardware", "integrity", "outputs"},
    "execute_goal": {"setup", "baseline", "measure", "persist_run", "persist_samples",
                     "spans", "attribution", "residual", "quality", "integrity",
                     "etl_phase", "etl_hardware"},  # D-2c-1: ETL runs in run_derived
}
DESIGN_15_3["save_single"] = DESIGN_15_3["save_pair"]


@pytest.fixture(autouse=True)
def fake_gate(monkeypatch):
    """Replace gate calls so tests do not depend on window state."""
    seq = iter(range(1, 10000))
    sent = []
    monkeypatch.setattr(st.gate, "next_seq", lambda: next(seq))
    monkeypatch.setattr(st.gate, "submit", lambda k, r: sent.append((k, r)))
    monkeypatch.setattr(st.gate, "is_inside", lambda: False)
    return sent


@pytest.mark.parametrize("gid", list(sg.GRAPHS))
def test_graph_nodes_match_design_and_acyclic(gid):
    """Addendum rule 4: nodes equal design 15.3; graph acyclic."""
    assert {n["stage_id"] for n in sg.GRAPHS[gid]["nodes"]} == DESIGN_15_3[gid]
    assert len(sg.topological_order(gid)) == len(DESIGN_15_3[gid])


def test_hash_stable():
    """Same definition, same hash; hash is 64 hex chars."""
    assert sg.graph_hash("save_pair") == sg.graph_hash("save_pair")
    assert len(sg.graph_hash("execute_goal")) == 64


def test_exception_reraised_and_recorded():
    """Context manager never swallows (design 15.7)."""
    r = st.StageRecorder("save_single")
    with pytest.raises(RuntimeError):
        with r.stage("persist_run"):
            raise RuntimeError("x")
    assert r.events["persist_run"]["status"] == "failed"


def test_swallowed_by_caller_unchanged():
    """Outer try/except keeps swallowing exactly as before."""
    r = st.StageRecorder("save_single")
    try:
        with r.stage("spans"):
            raise ValueError("silent span failure")
    except ValueError:
        pass
    assert r.events["spans"]["status"] == "failed"


def test_propagation_and_finalize():
    """Failed requires predecessor yields skipped_dependency with blocked_by."""
    r = st.StageRecorder("save_single")
    with r.stage("persist_run") as i:
        i["counts"] = {"runs": 1}
    r.fail("persist_samples", "test")
    r.finalize()
    assert r.events["attribution"]["status"] == "skipped_dependency"
    # blocked_by names the direct predecessor (etl_phase, itself blocked)
    assert r.events["etl_phase"]["blocked_by"] == "persist_samples"
    assert r.events["attribution"]["blocked_by"] == "etl_phase"
    assert r.events["residual"]["blocked_by"] == "attribution"
    assert all(e["status"] in st.TERMINAL for e in r.events.values())
    assert set(r.events) == DESIGN_15_3["save_single"]


def test_zero_rows_contract():
    """Unexpected zero rows fail; declared empty_ok yields outcome empty."""
    r = st.StageRecorder("save_single")
    with r.stage("persist_run") as i:
        i["counts"] = {"runs": 0}
    with r.stage("spans") as i:
        i["counts"] = {"spans": 0}
    assert r.events["persist_run"]["reason"] == "unexpected_zero_rows"
    assert r.events["spans"]["outcome"] == "empty"


def test_non_applicable_stage_emits_nothing():
    """baseline is not a save_pair node."""
    r = st.StageRecorder("save_pair")
    with r.stage("baseline"):
        pass
    assert "baseline" not in r.events


def test_persist_roundtrip_and_order():
    """Rows land in the v120 tables in event_seq order; graph idempotent."""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE runs (run_id INTEGER PRIMARY KEY)")
    con.execute("INSERT INTO runs VALUES (7)")
    con.execute("CREATE TABLE schema_version (version INTEGER, applied_at TEXT, description TEXT)")
    con.executescript(open(MIG).read())
    for _ in range(2):
        r = st.StageRecorder("execute_goal")
        with r.stage("setup"):
            pass
        r.attach_run_id(7)
        st.persist(r, lambda sql, rows: con.executemany(sql, rows))
    assert con.execute("SELECT COUNT(*) FROM stage_graph").fetchone()[0] == 1
    seqs = [x[0] for x in con.execute(
        "SELECT event_seq FROM stage_event WHERE run_uid=? ORDER BY rowid", (r.run_uid,))]
    assert seqs == sorted(seqs) and len(seqs) == 12


def test_persist_refused_inside_window(monkeypatch):
    """Master 5.1 rule 2: no store write inside [t0, t1]."""
    monkeypatch.setattr(st.gate, "is_inside", lambda: True)
    with pytest.raises(st.StageContractError):
        st.persist(st.StageRecorder("save_single"), lambda s, r: None)


def test_rollback_downgrades_held_stages(fake_gate):
    """A succeeded stage inside a failed transaction is failed rolled_back."""
    r = st.StageRecorder("save_single")
    with pytest.raises(RuntimeError):
        with r.tx_scope():
            with r.stage("persist_run") as i:
                i["counts"] = {"runs": 1}
            with r.stage("persist_samples"):
                raise RuntimeError("samples")
    assert r.events["persist_run"]["status"] == "failed"
    assert r.events["persist_run"]["reason"] == "rolled_back"
    assert r.events["persist_samples"]["reason"] == "exception"
    assert len(fake_gate) == 2  # submitted once, after the scope ended


def test_tx_success_submits_after_scope(fake_gate):
    """Nothing is submitted while the transaction is open."""
    r = st.StageRecorder("save_single")
    with r.tx_scope():
        with r.stage("persist_run") as i:
            i["counts"] = {"runs": 1}
        assert fake_gate == []
    assert r.events["persist_run"]["status"] == "succeeded" and len(fake_gate) == 1


def test_multi_part_stage_merges_worst_status():
    """etl_hardware runs in two parts; one row, worst status, summed counts."""
    r = st.StageRecorder("save_single")
    with r.stage("etl_hardware") as i:
        i["counts"] = {"nic_samples": 3}
    try:
        with r.stage("etl_hardware"):
            raise ValueError("aggregate")
    except ValueError:
        pass
    ev = r.events["etl_hardware"]
    assert ev["status"] == "failed" and ev["counts"] == {"nic_samples": 3}