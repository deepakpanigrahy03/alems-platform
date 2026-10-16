"""SG notice (RAF Addendum C): facts from the environment, never ground truth."""

from alems_plugin_bfcl.env import BFCLEnv
from alems_plugin_bfcl.sg_notice import build_notice, touched
from alems_plugin_bfcl.tasks import ground_truth, load_entry

TASK = "multi_turn_base_7"


def _env_after_turn0():
    env = BFCLEnv(load_entry(TASK))
    for call in ground_truth(TASK)[0]:
        env.call_string(call)
    return env


def test_touched_lists_called_instances_only():
    env = _env_after_turn0()
    names = touched(env, 0)
    assert names and all(n in env.instances for n in names)
    assert touched(env, len(env.call_log)) == []      # nothing called after the end


def test_tool_failure_notice_states_no_effect():
    env = _env_after_turn0()
    text = build_notice(env, 0, {"kind": "tool", "tool_name": "mkdir"})
    assert text.startswith("[state]")
    assert "mkdir failed and did not take effect" in text
    assert touched(env, 0)[0] in text


def test_llm_failure_notice_has_no_failed_call_line():
    env = _env_after_turn0()
    text = build_notice(env, 0, {"kind": "llm", "tool_name": None})
    assert "did not take effect" not in text


def test_no_calls_in_turn():
    env = _env_after_turn0()
    text = build_notice(env, len(env.call_log), {"kind": "llm"})
    assert "No tool was called in this turn" in text


def test_never_mentions_ground_truth():
    env = _env_after_turn0()
    text = build_notice(env, 0, {"kind": "tool", "tool_name": "mkdir"}).lower()
    for word in ("ground truth", "expected", "checker", "correct answer"):
        assert word not in text


def test_failed_first_call_still_shows_its_tool_state():
    env = _env_after_turn0()
    text = build_notice(env, len(env.call_log), {"kind": "tool", "tool_name": "mkdir"})
    assert "No tool was called" not in text
    assert "Current state of the tools involved in this turn" in text
    assert "mkdir failed and did not take effect" in text
