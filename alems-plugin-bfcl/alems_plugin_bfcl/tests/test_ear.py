"""Unit tests for alems_plugin_bfcl.decider.ear pure parts (no server, no store), on R5's selector layout."""
import json
import math

from alems_plugin_bfcl.decider import ear as E

# R5 selector_lr_v2.json layout: cls= features then calls_done, coef a list in feature order
SEL = {"features": ["cls=rate_limit", "cls=tool_error", "calls_done"], "coef": [2.0, 0.0, 0.1],
       "intercept": -1.0, "threshold": 0.5, "params_sha256": "ab" * 32}


def write(tmp_path, obj, name="s.json"):
    p = tmp_path / name
    p.write_text(json.dumps(obj))
    return str(p)


def test_load_selector_r5_layout(tmp_path):
    sel, sha = E.load_selector(write(tmp_path, SEL))
    assert len(sha) == 64 and sel["classes"] == ["rate_limit", "tool_error"]
    assert sel["coef"] == {"cls=rate_limit": 2.0, "cls=tool_error": 0.0, "calls_done": 0.1}
    assert E.load_selector(None) == (None, None)
    assert E.load_selector(str(tmp_path / "missing.json")) == (None, None)


def test_load_selector_refuses_bad_layout(tmp_path):
    for bad in (dict(SEL, coef=[1.0, 2.0]),                                   # length mismatch
                dict(SEL, features=["rate_limit", "cls=tool_error", "calls_done"]),   # unknown feature name
                {k: v for k, v in SEL.items() if k != "intercept"}):            # missing field
        assert E.load_selector(write(tmp_path, bad, "b.json")) == (None, None)


def test_one_hot_and_order(tmp_path):
    sel, _ = E.load_selector(write(tmp_path, SEL))
    x = E.features(sel, "rate_limit", 4)
    assert list(x) == SEL["features"] and x == {"cls=rate_limit": 1.0, "cls=tool_error": 0.0, "calls_done": 4.0}
    assert E.features(sel, "timeout", 0) == {"cls=rate_limit": 0.0, "cls=tool_error": 0.0, "calls_done": 0.0}


def test_probability_and_strict_threshold(tmp_path):
    sel, _ = E.load_selector(write(tmp_path, SEL))
    p = E.probability(sel, E.features(sel, "rate_limit", 0))
    assert abs(p - 1 / (1 + math.exp(-1.0))) < 1e-12        # z = -1 + 2
    p = E.probability(sel, E.features(sel, "tool_error", 10))
    assert abs(p - 0.5) < 1e-12 and not (p > sel["threshold"])   # exactly 0.5 goes to beta_mean


def test_r5_v2_numbers():
    """R5's published v2 selector: tool_error and json_parse pick tokens at small calls done."""
    sel = {"features": ["cls=json_parse", "cls=rate_limit", "cls=silent_detected", "cls=timeout",
                        "cls=tool_error", "calls_done"],
           "coef": dict(zip(["cls=json_parse", "cls=rate_limit", "cls=silent_detected", "cls=timeout",
                             "cls=tool_error", "calls_done"],
                            [0.228720, 0.228754, -0.851333, 0.101987, 0.291718, 0.0018306])),
           "intercept": 0.0088811, "threshold": 0.5}
    for cls in ("json_parse", "tool_error"):
        for calls in (6, 33):
            assert E.probability(sel, E.features(sel, cls, calls)) > 0.5
    assert E.probability(sel, E.features(sel, "silent_detected", 33)) < 0.5
