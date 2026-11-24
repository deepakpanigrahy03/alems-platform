"""Tests for core/serving/serving_snapshot_sampler.py (45.2d), no engine needed."""
import time

from core.serving import serving_snapshot_sampler as sss


class _Cache:
    capacity_tokens = None
    occupied_tokens = None
    occupancy_fraction = 0.25
    hit_rate_aggregate = None
    num_evictions = None


class _Queue:
    active = 1
    waiting = 0
    completed = None
    rejected = None


class _Adapter:
    def __init__(self, fail=False):
        self.fail = fail

    def get_cache_state(self):
        if self.fail:
            raise RuntimeError("down")
        return _Cache()

    def get_queue_state(self):
        return _Queue()


def _sampler(adapters):
    s = sss.ServingSnapshotSampler(interval_s=0.05, configs=[])
    s._build_adapters = lambda: adapters
    return s


def test_rows_kept_separate_and_nulls_kept():
    cfg = {"type": "vllm", "endpoint": "http://localhost:8000", "provider": "vllm_local"}
    s = _sampler([(cfg, _Adapter())])
    s.start()
    time.sleep(0.2)
    rows = s.stop()
    kv = [r for r in rows if r["snapshot_type"] == "kv_cache"]
    q = [r for r in rows if r["snapshot_type"] == "queue"]
    assert kv and len(kv) == len(q)
    assert kv[0]["kv_occupancy_fraction"] == 0.25 and kv[0]["kv_capacity_tokens"] is None
    assert "queue_active" not in kv[0] and "kv_occupancy_fraction" not in q[0]
    assert '"method_id": "kv_occupancy_vllm"' in kv[0]["extra_json"]


def test_no_engine_no_thread_no_rows():
    s = _sampler([])
    s.start()
    assert s._thread is None and s.stop() == []


def test_failing_engine_skipped():
    cfg = {"type": "sglang", "endpoint": "x", "provider": "sglang_local"}
    s = _sampler([(cfg, _Adapter(fail=True))])
    s.start()
    time.sleep(0.12)
    assert s.stop() == []
