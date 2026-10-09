"""
S1 unit tests for alems-plugin-bfcl (SPEC_RAF_LH_01 v2, section 4).

Run: python -m pytest alems-plugin-bfcl/tests -q
No model, no store, no network: everything runs on the vendored data.
"""

import ast
import json
import os
import time

import pytest

from alems_plugin_bfcl import tasks
from alems_plugin_bfcl.env import BFCLEnv, ERROR_PREFIX, format_call, parse_call
from alems_plugin_bfcl.provider import BFCLToolProvider
from alems_plugin_bfcl.scorer import BFCLStateScorer
from alems_sdk.tools import ToolExecutionContext

CTX = ToolExecutionContext(db_path="")
BASE_IDS = tasks.list_ids("base")


def _replay(task_id):
    """Replay the ground truth of one task; return (env, flat call list)."""
    env = BFCLEnv(tasks.load_entry(task_id))
    calls = [c for turn in tasks.ground_truth(task_id) for c in turn]
    for c in calls:
        env.call_string(c)
    return env, calls


def _payload(task_id, turns):
    """Scorer input in the provider's canonical form."""
    return json.dumps({"task_id": task_id, "turns": turns})


# ── 1. data ──────────────────────────────────────────────────────────
def test_all_entries_load_and_build():
    """All 400 base and long context entries load and their envs build."""
    assert len(BASE_IDS) == 200
    assert len(tasks.list_ids("long_context")) == 200
    for tid in BASE_IDS + tasks.list_ids("long_context"):
        env = BFCLEnv(tasks.load_entry(tid))
        assert set(env.instances) == set(tasks.load_entry(tid)["involved_classes"])
        assert len(tasks.ground_truth(tid)) == len(tasks.load_entry(tid)["question"])


def test_ground_truth_calls_parse():
    """Every ground truth call parses and round trips through format_call."""
    for tid in BASE_IDS:
        for c in (c for turn in tasks.ground_truth(tid) for c in turn):
            name, args, kwargs = parse_call(c)
            assert parse_call(format_call(name, args, kwargs)) == (name, args, kwargs)


def test_alems_task_dict():
    """to_alems_task keeps turns, classes, scorer and the upstream commit."""
    t = tasks.to_alems_task("multi_turn_base_0")
    assert t["scorer"] == "bfcl_state" and t["expected"] == "multi_turn_base_0"
    assert len(t["turns"]) == 4 and t["upstream_commit"] == tasks.UPSTREAM_COMMIT


# ── 2. determinism ───────────────────────────────────────────────────
def test_replay_is_deterministic():
    """Two ground truth replays give equal full state hashes (all 200)."""
    for tid in BASE_IDS:
        assert _replay(tid)[0].state_hash() == _replay(tid)[0].state_hash(), tid


def test_initial_hash_is_stable():
    """A fresh env hashes the same every time (no clock or id leakage)."""
    for tid in BASE_IDS[:50]:
        e = tasks.load_entry(tid)
        assert BFCLEnv(e).state_hash() == BFCLEnv(e).state_hash(), tid


# ── 3. scorer ────────────────────────────────────────────────────────
def test_scorer_accepts_ground_truth_all():
    """Ground truth as model output scores 1.0 on all 200 base tasks."""
    s = BFCLStateScorer()
    for tid in BASE_IDS:
        score, conf, why = s.score(_payload(tid, tasks.ground_truth(tid)), tid)
        assert (score, conf) == (1.0, 1.0), (tid, why)


def _mutations(tid):
    """Yield (label, turns) with one state changing call dropped."""
    gt = tasks.ground_truth(tid)
    for ti, turn in enumerate(gt):
        for ci in range(len(turn)):
            turns = [list(t) for t in gt]
            del turns[ti][ci]
            yield "drop t%d c%d" % (ti, ci), turns


def test_scorer_rejects_dropped_calls():
    """
    Dropping a ground truth call must fail unless that call neither
    changes state nor produces a needed result; at least one drop per
    task must fail (20 tasks).
    """
    s = BFCLStateScorer()
    for tid in BASE_IDS[:20]:
        verdicts = [s.score(_payload(tid, turns), tid)[0] for _, turns in _mutations(tid)]
        assert 0.0 in verdicts, tid


def test_scorer_rejects_altered_argument():
    """Changing a string argument of a mutating call fails the task."""
    tid = "multi_turn_base_0"
    gt = [list(t) for t in tasks.ground_truth(tid)]
    gt[0][1] = "mkdir(dir_name='tmp_other')"
    assert BFCLStateScorer().score(_payload(tid, gt), tid)[0] == 0.0


def test_scorer_rejects_missing_turn():
    """An agent that stops after turn 0 fails (missing turns are empty)."""
    tid = "multi_turn_base_0"
    gt = tasks.ground_truth(tid)
    score, conf, why = BFCLStateScorer().score(_payload(tid, gt[:1]), tid)
    assert (score, conf) == (0.0, 1.0) and "turn 1" in why


def test_empty_ground_truth_turn_not_judged():
    """Calls in a turn whose ground truth is empty are executed, not judged."""
    tid = "multi_turn_base_167"
    gt = [list(t) for t in tasks.ground_truth(tid)]
    assert gt[4] == []
    gt[4] = ["no_such_tool()"]
    assert BFCLStateScorer().score(_payload(tid, gt), tid)[0] == 1.0


def test_scorer_never_raises():
    """Malformed input returns scorer_failed with confidence 0."""
    s = BFCLStateScorer()
    for bad in ("", "not json", json.dumps({"turns": "x"})):
        score, conf, why = s.score(bad, "multi_turn_base_0")
        assert (score, conf) == (0.0, 0.0) and why.startswith("scorer_failed")
    assert s.score(_payload("multi_turn_base_1", []), "multi_turn_base_0")[1] == 0.0


# ── 4. snapshot and restore ──────────────────────────────────────────
def test_restore_initial_equals_fresh():
    """After calls, restore_initial gives the fresh initial hash (FR)."""
    for tid in BASE_IDS[:30]:
        env, _ = _replay(tid)
        env.restore_initial()
        assert env.state_hash() == BFCLEnv(tasks.load_entry(tid)).state_hash(), tid
        assert env.calls_made == 0


def test_restore_before_k_matches_prefix_replay():
    """restore_before(k) equals replaying calls 1..k-1 on a fresh env (SR)."""
    for tid in BASE_IDS[:30]:
        env, calls = _replay(tid)
        k = max(1, len(calls) // 2)
        env.restore_before(k)
        ref = BFCLEnv(tasks.load_entry(tid))
        for c in calls[:k - 1]:
            ref.call_string(c)
        assert env.state_hash() == ref.state_hash(), tid
        assert env.calls_made == k - 1


def test_resume_after_restore_reaches_same_final_state():
    """SR then rerunning calls k..n reaches the uninterrupted final state."""
    for tid in BASE_IDS[:30]:
        env, calls = _replay(tid)
        final = env.state_hash()
        k = max(1, len(calls) // 2)
        env.restore_before(k)
        for c in calls[k - 1:]:
            env.call_string(c)
        assert env.state_hash() == final, tid


def test_restore_before_out_of_range():
    """restore_before rejects ordinals that were never executed."""
    env = BFCLEnv(tasks.load_entry("multi_turn_base_0"))
    with pytest.raises(IndexError):
        env.restore_before(1)


# ── 5. provider ──────────────────────────────────────────────────────
def test_provider_end_to_end_ground_truth():
    """Provider executes ground truth by turn; its output scores 1.0."""
    p = BFCLToolProvider()
    tid = "multi_turn_base_0"
    p.load_task(tid)
    for ti, turn in enumerate(tasks.ground_truth(tid)):
        p.begin_turn(ti)
        for c in turn:
            name, args, kwargs = parse_call(c)
            assert not args or name == "sort", c  # only sort() is positional here
            if args:
                kwargs = {"file_name": args[0]}
            r = p.execute(name, kwargs, CTX)
            assert r.tool_name == name and r.duration_ns >= 0
    assert BFCLStateScorer().score(p.scorer_output(), tid)[0] == 1.0


def test_provider_failure_paths_never_raise():
    """Unknown tool, bad args, forbidden name, no task: success False."""
    p = BFCLToolProvider()
    assert p.execute("ls", {}, CTX).success is False      # no task loaded
    p.load_task("multi_turn_base_0")
    for name, args in (("no_such_tool", {}), ("cd", {"bogus": 1}), ("kill", {})):
        r = p.execute(name, args, CTX)
        assert r.success is False and r.error.startswith(ERROR_PREFIX)


def test_provider_tools_are_json_schema():
    """Definitions use JSON Schema types and honor excluded_function."""
    p = BFCLToolProvider()
    p.load_task("multi_turn_base_0")
    defs = p.get_tools()
    assert defs and all(d.parameters.get("type") == "object" for d in defs)
    blob = json.dumps([d.parameters for d in defs])
    assert '"dict"' not in blob and '"float"' not in blob
    assert len(BFCLToolProvider().get_tools()) > len(defs)


def test_provider_sr_restores_turn_marks():
    """restore_before drops turns that started after the restored point."""
    p = BFCLToolProvider()
    p.load_task("multi_turn_base_0")
    p.begin_turn(0)
    p.execute("cd", {"folder": "document"}, CTX)
    p.begin_turn(1)
    p.execute("ls", {}, CTX)
    p.restore_before(2)
    assert p.calls_by_turn() == [["cd(folder='document')"], []]


def test_snapshot_cost_report(capsys):
    """Times the per call snapshot (deep copy); printed for the handover."""
    p = BFCLToolProvider()
    worst = 0
    for tid in BASE_IDS:
        p.load_task(tid)
        t0 = time.perf_counter_ns()
        p.env.snapshots.append(__import__("copy").deepcopy(p.env.instances))
        worst = max(worst, time.perf_counter_ns() - t0)
    print("\nsnapshot deepcopy worst case over 200 base tasks: %.3f ms" % (worst / 1e6))


# ── 6. import guard (INV-14) ─────────────────────────────────────────
def test_imports_only_sdk_and_stdlib():
    """No module of the package imports core, scripts, or bfcl_eval."""
    root = os.path.join(os.path.dirname(__file__), "..", "alems_plugin_bfcl")
    for dirpath, _, files in os.walk(root):
        for f in (f for f in files if f.endswith(".py")):
            tree = ast.parse(open(os.path.join(dirpath, f), encoding="utf-8").read())
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names = [node.module or ""]
                bad = [n for n in names if n.split(".")[0] in ("core", "scripts", "bfcl_eval")]
                assert not bad, (f, bad)
