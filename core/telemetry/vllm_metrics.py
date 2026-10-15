"""
vLLM server cache statistics from /metrics (RAF R2, coordinator Oct 15).

Read between attempts, never inside a measurement window. One reading:
    kv_cache_usage_perc      fraction of KV cache blocks in use (0..1)
    prefix_cache_queries     cumulative prompt tokens looked up in the prefix cache
    prefix_cache_hits        cumulative prompt tokens served from it
    ts_ns                    wall clock of the reading

A reading never raises: on any failure it returns None and the caller stores
nothing (MIC-1: unavailable is NULL, never 0).
"""

import re
import time
import urllib.request
from typing import Dict, Optional

_LINE = re.compile(r"^vllm:(kv_cache_usage_perc|prefix_cache_queries_total|"
                   r"prefix_cache_hits_total)\{[^}]*\}\s+([0-9.eE+-]+)\s*$")
_KEYS = {"kv_cache_usage_perc": "kv_cache_usage_perc",
         "prefix_cache_queries_total": "prefix_cache_queries",
         "prefix_cache_hits_total": "prefix_cache_hits"}


def metrics_url(base_url: str) -> str:
    """Model binding base URL ends in /v1; /metrics lives at the server root."""
    root = (base_url or "").rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    return root + "/metrics"


def parse(text: str) -> Optional[Dict[str, float]]:
    """Extract the three values; None when any is missing."""
    out: Dict[str, float] = {}
    for line in text.splitlines():
        m = _LINE.match(line.strip())
        if m:
            # Several engines would repeat a series; sum counters, keep max usage.
            key, val = _KEYS[m.group(1)], float(m.group(2))
            if key == "kv_cache_usage_perc":
                out[key] = max(out.get(key, 0.0), val)
            else:
                out[key] = out.get(key, 0.0) + val
    return out if len(out) == 3 else None


def read(base_url: str, timeout_s: float = 2.0) -> Optional[Dict[str, float]]:
    """One reading of the server cache statistics, or None."""
    try:
        # Proxies bypassed (lab proxy breaks localhost), as for the cache reset.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(metrics_url(base_url), timeout=timeout_s) as resp:
            vals = parse(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None
    if vals is None:
        return None
    vals["ts_ns"] = time.time_ns()
    return vals


def snapshot_row(vals: Dict[str, float]) -> Dict[str, object]:
    """cache_state_snapshots columns (without run_id, attempt_id) for one reading."""
    q, h = vals["prefix_cache_queries"], vals["prefix_cache_hits"]
    return {"timestamp_ns": int(vals["ts_ns"]), "engine_name": "vllm",
            "cache_type": "prefix_cache", "capacity_tokens": None,
            "occupied_tokens": None,
            "occupancy_fraction": float(vals["kv_cache_usage_perc"]),
            # Cumulative since server start; per attempt deltas come from the
            # raw counters kept in the RAF ledger (prepare records).
            "hit_rate_aggregate": (h / q) if q else None}
