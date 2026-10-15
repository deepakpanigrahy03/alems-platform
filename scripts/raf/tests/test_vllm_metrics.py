"""vLLM /metrics cache statistics parser (RAF R2)."""

from core.telemetry import vllm_metrics as vm

TEXT = """# HELP vllm:kv_cache_usage_perc KV cache usage
vllm:kv_cache_usage_perc{engine="0",model_name="Q"} 0.25
vllm:prefix_cache_queries_total{engine="0",model_name="Q"} 724417.0
vllm:prefix_cache_queries_created{engine="0",model_name="Q"} 1.79e+09
vllm:prefix_cache_hits_total{engine="0",model_name="Q"} 651808.0
vllm:external_prefix_cache_hits_total{engine="0",model_name="Q"} 0.0
"""


def test_parse_three_values():
    v = vm.parse(TEXT)
    assert v == {"kv_cache_usage_perc": 0.25, "prefix_cache_queries": 724417.0,
                 "prefix_cache_hits": 651808.0}


def test_parse_missing_value_is_none():
    assert vm.parse("vllm:kv_cache_usage_perc{a=\"b\"} 0.1\n") is None


def test_snapshot_row():
    v = dict(vm.parse(TEXT), ts_ns=123)
    r = vm.snapshot_row(v)
    assert r["occupancy_fraction"] == 0.25 and r["engine_name"] == "vllm"
    assert abs(r["hit_rate_aggregate"] - 651808.0 / 724417.0) < 1e-12
    assert r["timestamp_ns"] == 123


def test_url_and_unreachable():
    assert vm.metrics_url("http://localhost:8000/v1") == "http://localhost:8000/metrics"
    assert vm.read("http://127.0.0.1:9/v1", timeout_s=0.2) is None
