"""Unit tests for alems_plugin_bfcl.sweep (no server, no store)."""
import pytest

from alems_plugin_bfcl import sweep as s


def fake_count(text):
    return len(text.split())          # one token per word


def test_parse_task():
    assert s.parse_task("raf_sweep_2048_uncached") == (2048, "uncached")
    assert s.parse_task("raf_sweep_32768_cached") == (32768, "cached")
    for bad in ("bfcl_multi_turn_base_1", "raf_sweep_2048_hot", "raf_sweep_x_cached", None):
        with pytest.raises(ValueError):
            s.parse_task(bad)


def test_server_root():
    assert s.server_root("http://localhost:8000/v1") == "http://localhost:8000"
    assert s.server_root("http://localhost:8000/v1/chat/completions") == "http://localhost:8000"
    assert s.server_root("http://localhost:8000/") == "http://localhost:8000"


def test_build_prompt_exact_and_unique():
    t1, n1 = s.build_prompt(500, "goal_a", fake_count)
    t2, n2 = s.build_prompt(500, "goal_b", fake_count)
    t3, _ = s.build_prompt(500, "goal_a", fake_count)
    assert n1 == 500 and n2 == 500
    assert t1 == t3                   # same goal key: same prompt (the cached warm call matches)
    assert t1.split()[0] != t2.split()[0]   # different goal key: different prefix (no cross goal reuse)


def usage(prompt, completion, cached=None):
    u = {"prompt_tokens": prompt, "completion_tokens": completion}
    if cached is not None:
        u["prompt_tokens_details"] = {"cached_tokens": cached}
    return u


OK = {"ok": True}


def test_check_clean():
    assert s.check("uncached", 2048, usage(2048, 64), OK) == []          # miss: no details
    assert s.check("uncached", 2048, usage(2047, 64, 16), OK) == []
    assert s.check("cached", 2048, usage(2048, 64, 2032), OK) == []


def test_check_codes():
    assert s.SWEEP_OUTPUT_LEN in s.check("uncached", 2048, usage(2048, 63), OK)
    assert s.SWEEP_PROMPT_LEN in s.check("uncached", 2048, usage(2000, 64), OK)
    assert s.SWEEP_CACHE_MODE in s.check("uncached", 2048, usage(2048, 64, 2000), OK)
    assert s.SWEEP_CACHE_MODE in s.check("cached", 2048, usage(2048, 64), OK)
    assert s.SWEEP_PREPARE in s.check("cached", 2048, usage(2048, 64, 2032), {"ok": False})
    assert set(s.check("cached", 2048, None, None)) >= {s.SWEEP_PREPARE, s.SWEEP_OUTPUT_LEN, s.SWEEP_PROMPT_LEN}


def test_classify_shape():
    plan = {"task": "raf_sweep_4096_cached", "policy": "B0"}
    out = s.classify(None, 1, plan, {1: OK}, {1: {"sweep_usage": usage(4096, 64, 4080)}})
    assert out["codes"] == [] and out["detail"]["mode"] == "cached"
    out = s.classify(None, 1, {"task": "bfcl_multi_turn_base_1"}, {}, {})
    assert out["codes"] == [s.SWEEP_PREPARE]
