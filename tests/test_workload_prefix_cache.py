"""Workload plugin: server prefix cache counters per step (no server needed)."""
from alems_plugin_workload import client
from alems_plugin_workload.outcome import prefix_cache_results

METRICS = """# HELP vllm:prefix_cache_queries_total Prefix cache queries
# TYPE vllm:prefix_cache_queries_total counter
vllm:prefix_cache_queries_total{engine="0",model_name="m"} 30547.0
vllm:prefix_cache_hits_total{engine="0",model_name="m"} 120.0
vllm:prefix_cache_hits_total{engine="1",model_name="m"} 80.0
vllm:prefix_cache_hits_created 1.7e9
"""


def test_metric_sum_over_label_sets():
    assert client.metric_sum(METRICS, "vllm:prefix_cache_queries_total") == 30547.0
    assert client.metric_sum(METRICS, "vllm:prefix_cache_hits_total") == 200.0
    assert client.metric_sum(METRICS, "vllm:absent_total") is None


def test_metric_sum_unlabelled_and_timestamp():
    assert client.metric_sum("x_total 5 1700000000\n", "x_total") == 5.0


def test_results_from_deltas():
    res = prefix_cache_results((1000.0, 100.0), (5000.0, 300.0))
    assert res["prefix_cache_queried_tokens"] == (4000, "tokens")
    assert res["prefix_cache_hit_tokens"] == (200, "tokens")
    assert res["prefix_cache_hit_share"] == (0.05, None)


def test_unknown_or_reset_counters_give_no_rows():
    assert prefix_cache_results((None, None), (None, None)) == {}
    assert prefix_cache_results((5000.0, 300.0), (10.0, 0.0)) == {}  # server restarted
    assert prefix_cache_results((100.0, None), (100.0, None)) == {"prefix_cache_queried_tokens": (0, "tokens")}


def test_counters_unreachable_server():
    assert client.prefix_cache_counters("http://127.0.0.1:9", "a", "b", 0.5) == (None, None)
