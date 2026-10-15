"""Tests for alems_plugin_bfcl.corrupt (spec R1 section 4.1 plus plausibility)."""

import json

from alems_plugin_bfcl import corrupt as C

ERR = "Error during execution: "


def test_seed_deterministic_and_distinct():
    assert C.seed_of("g1", "silent_tool_output", 2) == C.seed_of("g1", "silent_tool_output", 2)
    assert C.seed_of("g1", "silent_tool_output", 2) != C.seed_of("g1", "silent_tool_output", 3)
    assert C.seed_of("g1", "silent_tool_output", 2) != C.seed_of("g1", "wrong_model_step", 2)


def test_leaves_skip_bool_and_none_and_sorted():
    v = {"b": [1, True], "a": None, "c": "x", "d": 2.5}
    paths = [C.path_text(p) for p, _ in C.leaves(v)]
    assert paths == sorted(paths)
    assert ("/b[1]" not in paths) and ("/a" not in paths)
    assert "/b[0]" in paths and "/c" in paths and "/d" in paths


def test_int_rule():
    for s in range(6):
        assert C.replacement(10, s, []) == (10 + 1 + s % 3, C.R_INT)


def test_float_rule_keeps_decimals_and_changes():
    r, rule = C.replacement(2.5, 0, [])
    assert rule == C.R_FLOAT and r != 2.5 and len(repr(r).split(".")[1]) == 1
    assert C.replacement(1.25, 0, [])[0] == round(1.875, 2)
    assert C.replacement(0.0, 0, [])[0] != 0.0


def test_str_rule_order():
    assert C.replacement("alpha", 7, ["alpha", "beta"], ["gamma"]) == ("beta", C.R_SWAP_LOCAL)
    assert C.replacement("academic_hub", 7, ["academic_hub"],
                         ["academic_venture", "academic_hub"]) == ("academic_venture", C.R_SWAP_SEEN)
    assert C.replacement("abc", 7, ["abc"]) == ("cba", C.R_REVERSE)
    assert C.replacement("aba", 7, ["aba"]) == ("abax", C.R_SUFFIX)
    assert C.replacement("a", 7, []) == ("ax", C.R_SUFFIX)


def test_seen_pool_ignores_empty_and_self():
    assert C.replacement("x1", 3, ["x1"], ["", "x1"])[1] == C.R_REVERSE


def test_type_preserved_and_value_changes():
    v = {"x": 3, "y": 1.5, "z": "q", "w": ["m", "n"]}
    for s in range(50):
        new, d = C.corrupt_value(v, s, ["seen_a"])
        assert d["applied"] and d["old"] != d["new"] and d["rule"]
        assert type(d["old"]) is type(d["new"])
        assert v == {"x": 3, "y": 1.5, "z": "q", "w": ["m", "n"]}  # input untouched
        assert sum(1 for k in v if new[k] != v[k]) == 1


def test_deterministic_same_seed_and_pool():
    v = {"a": 1, "b": "s", "c": [2, 3]}
    assert C.corrupt_value(v, 99, ["t"]) == C.corrupt_value(v, 99, ["t"])


def test_args_not_applicable_and_seen_swap():
    assert C.corrupt_args({}, 1)[0] is None
    assert C.corrupt_args({"__invalid_arguments__": "x"}, 1)[0] is None
    new, d = C.corrupt_args({"dir_name": "academic_hub"}, 1, ["academic_venture"])
    assert new == {"dir_name": "academic_venture"} and d["rule"] == C.R_SWAP_SEEN


def test_result_rules():
    assert C.corrupt_result(ERR + "boom", 1, ERR)[0] is None
    assert C.corrupt_result("plain text", 1, ERR)[0] is None
    assert C.corrupt_result("5", 1, ERR)[0] is None
    text = json.dumps({"balance": 120, "currency": "USD"})
    new, d = C.corrupt_result(text, 3, ERR)
    assert d["applied"] and json.loads(new) != json.loads(text)
    assert set(json.loads(new)) == {"balance", "currency"}


def test_strings_of():
    assert sorted(C.strings_of({"a": ["x", 1, {"b": "y"}], "c": True})) == ["x", "y"]
