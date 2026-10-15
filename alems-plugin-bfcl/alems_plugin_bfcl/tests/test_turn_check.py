"""Tests for alems_plugin_bfcl.turn_check (spec R1 section 4.3)."""

from alems_plugin_bfcl.env import BFCLEnv
from alems_plugin_bfcl.tasks import ground_truth, load_entry
from alems_plugin_bfcl.turn_check import DetectedFailure, check_turn

TASK = "multi_turn_base_0"


def _env():
    return BFCLEnv(load_entry(TASK))


def _replay(env, calls):
    for c in calls:
        env.call_string(c)


def test_correct_turns_pass():
    env, gt = _env(), ground_truth(TASK)
    for turn_calls in gt:
        start = env.calls_made
        _replay(env, turn_calls)
        rec = check_turn(env, start, turn_calls)
        assert rec["ok"], rec
        assert rec["ms"] >= 0 and rec["gt_calls"] == len(turn_calls)


def test_corrupted_state_fails():
    env, gt = _env(), ground_truth(TASK)
    start = env.calls_made
    _replay(env, gt[0])
    first = next(iter(env.instances.values()))
    first.raf_poison = 1  # public attribute: changes the public hash
    assert not check_turn(env, start, gt[0])["ok"]


def test_earlier_divergence_not_blamed():
    env, gt = _env(), ground_truth(TASK)
    _replay(env, gt[0])
    first = next(iter(env.instances.values()))
    first.raf_poison = 1  # divergence before turn 1 opens
    start = env.calls_made
    _replay(env, gt[1])
    assert check_turn(env, start, gt[1])["ok"]


def test_detected_failure_fields():
    exc = DetectedFailure(2, {"ok": False})
    assert exc.cls == "silent_detected" and exc.turn == 2
