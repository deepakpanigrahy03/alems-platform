"""Accepted wrong step (E2b): the corrupted call succeeds and changes state differently."""

from alems_plugin_bfcl.env import BFCLEnv, ERROR_PREFIX, state_hash
from alems_plugin_bfcl.tasks import ground_truth, load_entry
from alems_plugin_bfcl.wrong_step import _trial, candidates, choose

TASK = "multi_turn_base_7"


def _env():
    env = BFCLEnv(load_entry(TASK))
    for call in ground_truth(TASK)[0]:
        env.call_string(call)
    return env


def test_accepted_candidate_changes_state_and_succeeds():
    env = _env()
    args = {"dir_name": "notes_new"}
    new, detail = choose(env, "mkdir", args, seed=1, seen=["academic_hub", "drafts"])
    assert new is not None and detail["accepted"] and detail["applied"]
    out, h = _trial(env, "mkdir", new)
    _, h_orig = _trial(env, "mkdir", args)
    assert not out.startswith(ERROR_PREFIX) and h != h_orig
    assert detail["accept_ms"] >= 0


def test_trials_never_touch_the_real_environment():
    env = _env()
    before = state_hash(env.instances, public_only=True)
    calls = len(env.call_log)
    choose(env, "mkdir", {"dir_name": "notes_new"}, seed=1, seen=["drafts"])
    assert state_hash(env.instances, public_only=True) == before and len(env.call_log) == calls


def test_read_call_is_not_applicable():
    env = _env()
    new, detail = choose(env, "pwd", {}, seed=1, seen=["drafts"])
    assert new is None and detail["reason"] == "not_accepted" and not detail["applied"]


def test_swaps_come_before_rule_values():
    cands = candidates({"dir_name": "a"}, seed=3, seen=["b", "c", 5])
    rules = [d["rule"] for _, d in cands]
    assert rules[0] == "swap_existing" and "swap_existing" in rules
    assert all(d["new"] in ("b", "c") for _, d in cands if d["rule"] == "swap_existing")
