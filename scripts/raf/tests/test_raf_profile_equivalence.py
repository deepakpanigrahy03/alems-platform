"""
Condition b: a profile generated from e1_gn100.yaml yields exactly the queue
the pilot schedule builds from the same config (ids, positions, goals).
Also covers the family form and B0 scope.
"""

import copy
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, ENGINE)
sys.path.insert(0, os.path.join(HERE, ".."))

import make_raf_profiles as mk  # noqa: E402
import pilot_schedule as ps  # noqa: E402
from core.execution import schedule as sch  # noqa: E402

PILOT_KEYS = ("task", "cls", "phase", "point", "ordinal", "policy")


def _load(name):
    with open(os.path.join(HERE, "..", name)) as fh:
        return yaml.safe_load(fh)


def _same(cfg):
    old = ps.build_queue(copy.deepcopy(cfg))
    new = sch.build_queue(mk.schedule_from(copy.deepcopy(cfg)))
    assert [(i["id"], i["position"], i["kind"]) for i in old] == \
           [(i["id"], i["position"], i["kind"]) for i in new]
    for a, b in zip(old, new):
        assert [tuple(g[k] for k in PILOT_KEYS) for g in a["goals"]] == \
               [tuple(g[k] for k in PILOT_KEYS) for g in b["goals"]]
        assert all(g["max_injections"] == 1 and g["attempts"] == 2
                   and not g["turn_check"] and g["tool_name"] is None
                   for g in b["goals"] if g["policy"] != "B0")
    return sum(len(i["goals"]) for i in new)


def test_e1_profile_queue_equals_pilot_schedule():
    cfg = _load("e1_gn100.yaml")
    cfg["points"] = ["early", "late"]       # the driver's default cut
    assert _same(cfg) > 0


def test_e1r2_profile_queue_equals_pilot_schedule():
    cfg = _load("e1r2_gn100.yaml")
    cfg["points"] = ["early", "late"]
    assert _same(cfg) > 0


def test_pilot_config_equivalent_too():
    cfg = _load("pilot_gn100.yaml")
    cfg["points"] = ["early", "late"]
    assert _same(cfg) == 350


def test_family_form_b0_checks_only():
    fam = {"seeds": {"block_order": 1, "policy_order": 2, "model_seed": 0},
           "b0_repetitions": 3, "early_ordinal": 2,
           "families": {"clean": {"classes": {"rate_limit": "llm"},
                                  "options": ["RB_cold", "RB_warm"],
                                  "points": {"early": 1}},
                        "silent": {"classes": {"silent_tool_output": "tool"},
                                   "options": ["SR_warm"], "points": {"any": 2},
                                   "turn_check": True}},
           "tasks": {"a": {"stratum": "short", "families": ["clean"], "targets": {}},
                     "b": {"stratum": "long", "families": ["silent"],
                           "targets": {"silent": {"any": {"tool_name": "cat"}}}}}}
    s = mk.schedule_from(fam)
    assert s["b0_scope"] == "checks"
    q = sch.build_queue(s)
    assert {i["task"] for i in q if i["kind"] == "b0"} == {"b"}
    ids = {i["id"] for i in q}
    assert "a|rate_limit|early" in ids and "b|silent_tool_output|any|r2" in ids
