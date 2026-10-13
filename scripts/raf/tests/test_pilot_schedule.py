"""Tests for scripts/raf/pilot_schedule.py (pure, no store)."""

import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import pilot_schedule as ps  # noqa: E402


def _cfg():
    with open(os.path.join(os.path.dirname(HERE), "pilot_gn100.yaml")) as fh:
        return yaml.safe_load(fh)


def test_queue_size_matches_design():
    counts = ps.count_goals(ps.build_queue(_cfg()))
    assert counts == {"block": 320, "b0": 30}


def test_queue_is_deterministic():
    a = [i["id"] for i in ps.build_queue(_cfg())]
    b = [i["id"] for i in ps.build_queue(_cfg())]
    assert a == b


def test_every_block_has_each_policy_once():
    cfg = _cfg()
    for blk in ps.build_blocks(cfg):
        assert sorted(g["policy"] for g in blk["goals"]) == sorted(cfg["policies"])


def test_policy_order_varies_across_blocks():
    orders = {tuple(g["policy"] for g in b["goals"]) for b in ps.build_blocks(_cfg())}
    assert len(orders) > 1


def test_b0_never_injected():
    for item in ps.build_b0(_cfg()):
        g = item["goals"][0]
        assert g["policy"] == "B0" and g["ordinal"] is None and g["phase"] is None


def test_ordinals_follow_taskset():
    cfg = _cfg()
    assert ps.ordinal(cfg, "bfcl_multi_turn_base_98", "llm", "late") == 9
    assert ps.ordinal(cfg, "bfcl_multi_turn_base_98", "tool", "late") == 7
    assert ps.ordinal(cfg, "bfcl_multi_turn_base_7", "tool", "early") == 2


def test_cut_early_only_halves_blocks():
    cfg = _cfg()
    cfg["points"] = ["early"]
    assert ps.count_goals(ps.build_queue(cfg))["block"] == 160
