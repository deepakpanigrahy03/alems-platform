"""RAF R2 main schedule: families, repetitions, targets, arms, determinism."""

import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import main_schedule as ms  # noqa: E402

SIX = ["FR_cold", "FR_warm", "SR_cold", "SR_warm", "RB_cold", "RB_warm"]

CFG = {
    "seeds": {"block_order": 1, "policy_order": 2},
    "b0_repetitions": 3,
    "early_ordinal": 2,
    "families": {
        "clean": {"classes": {"rate_limit": "llm", "tool_error": "tool"},
                  "options": ["RB_cold", "RB_warm"], "points": {"early": 1, "late": 1}},
        "wrong_model_step": {"classes": {"wrong_model_step": "tool"},
                             "options": SIX, "points": {"early": 1, "late": 1},
                             "turn_check": True},
        "silent": {"classes": {"silent_tool_output": "tool"},
                   "options": SIX + ["SR_notice"], "points": {"any": 2},
                   "turn_check": True},
        "persistent": {"classes": {"persistent_rate_limit": "llm"},
                       "options": ["SR_warm", "SR_warm@escalate"],
                       "points": {"early": 1, "late": 1},
                       "max_injections": 2, "attempts": 3},
    },
    "tasks": {
        "t1": {"stratum": "short",
               "families": ["clean", "persistent", "wrong_model_step"],
               "targets": {"clean": {"late": {"llm": 5, "tool": 3}},
                           "wrong_model_step": {"early": {"tool_name": "mkdir"},
                                                "late": {"tool_name": "mv"}},
                           "persistent": {"early": {"llm": 2, "tool": 2},
                                          "late": {"llm": 5, "tool": 3}}}},
        "t2": {"stratum": "long", "families": ["silent", "wrong_model_step"],
               "targets": {"silent": {"any": {"tool_name": "cat"}},
                           "wrong_model_step": {"early": {"tool_name": "touch"}}}},
    },
}


def test_queue_deterministic():
    assert ms.build_queue(copy.deepcopy(CFG)) == ms.build_queue(copy.deepcopy(CFG))


def test_counts_per_family():
    c = ms.count_goals(ms.build_queue(CFG))
    assert c["family:clean"] == 2 * 2 * 2              # classes x points x options
    assert c["family:wrong_model_step"] == (2 + 1) * 6  # t2 has no late write
    assert c["family:silent"] == 2 * 7
    assert c["family:persistent"] == 2 * 2
    assert c["b0"] == 2 * 3                              # both tasks check turns


def test_repetition_ids_unique():
    ids = [b["id"] for b in ms.build_blocks(CFG)]
    assert "t2|silent_tool_output|any|r2" in ids
    assert len(ids) == len(set(ids))


def test_targets():
    blocks = {b["id"]: b for b in ms.build_blocks(CFG)}
    g = blocks["t1|rate_limit|early|r1"]["goals"][0]
    assert g["ordinal"] == 2 and g["tool_name"] is None
    assert blocks["t1|tool_error|late|r1"]["goals"][0]["ordinal"] == 3
    g = blocks["t1|wrong_model_step|late|r1"]["goals"][0]
    assert g["ordinal"] == "any" and g["tool_name"] == "mv" and g["turn_check"]
    g = blocks["t2|silent_tool_output|any|r1"]["goals"][0]
    assert g["ordinal"] == "any" and g["tool_name"] == "cat"
    assert "t2|wrong_model_step|late|r1" not in blocks


def test_persistent_arms_and_attempts():
    blocks = {b["id"]: b for b in ms.build_blocks(CFG)}
    goals = blocks["t1|persistent_rate_limit|late|r1"]["goals"]
    assert sorted(g["arm"] for g in goals) == ["escalate", "fixed"]
    assert all(g["policy"] == "SR_warm" and g["attempts"] == 3
               and g["max_injections"] == 2 and g["ordinal"] == 5 for g in goals)


def test_clean_only_has_no_b0():
    q = ms.build_queue(CFG, families=["clean"])
    assert {i["kind"] for i in q} == {"block"}
    assert {i["family"] for i in q} == {"clean"}


def test_parse_option():
    assert ms.parse_option("RB_cold") == ("RB_cold", "fixed")
    assert ms.parse_option("SR_warm@escalate") == ("SR_warm", "escalate")
