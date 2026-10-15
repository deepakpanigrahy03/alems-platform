"""Prefix cache reuse rows per LLM call (RAF R2, state_reuse_events)."""

from alems_plugin_bfcl.react import _state_reuse_rows


def test_one_row_per_call_with_counts():
    calls = [{"ordinal": 1, "prompt_tokens": 1000, "cached_tokens": 0, "cached_reported": True},
             {"ordinal": 2, "prompt_tokens": 1200, "cached_tokens": 960, "cached_reported": True}]
    rows = _state_reuse_rows(calls)
    assert len(rows) == 2
    assert rows[0]["tokens_reused"] == 0 and rows[0]["tokens_recomputed"] == 1000
    assert rows[0]["cache_hit"] == 0 and rows[0]["reuse_fraction"] == 0.0
    assert rows[1]["tokens_reused"] == 960 and rows[1]["tokens_recomputed"] == 240
    assert rows[1]["cache_hit"] == 1 and abs(rows[1]["reuse_fraction"] - 0.8) < 1e-9
    assert all(r["reuse_type"] == "prefix_cache" and r["reuse_source"] == "vllm" for r in rows)


def test_unreported_usage_is_null_not_zero():
    rows = _state_reuse_rows([{"ordinal": 1, "prompt_tokens": 500, "cached_tokens": 0,
                               "cached_reported": False},
                              {"ordinal": 2, "prompt_tokens": None, "cached_tokens": 0,
                               "cached_reported": True}])
    for r in rows:
        assert r["tokens_reused"] is None and r["tokens_recomputed"] is None
        assert r["reuse_fraction"] is None and r["cache_hit"] is None


def test_no_calls_no_rows():
    assert _state_reuse_rows([]) == []
