"""
serving_snapshot_sampler.py: KV cache and queue state of local serving engines
during a run.

Why this exists:
    KV occupancy is only meaningful while requests run: vLLM frees KV blocks
    when a request ends, so a snapshot taken after the run reads about zero.
    This sampler polls each configured local engine's /metrics (through its
    ServingEngineAdapter) on a background thread for the length of the
    measurement window and buffers the readings. It never touches the
    database (EEI-1, EEI-3): the harness hands the buffer to persistence in
    the run result, and persistence writes serving_runtime_snapshots rows
    with the run_id on every execution path.

Which engines: every local serving engine resolved by
core.serving.engine_config (plugin traits < models.yaml < ~/.alemsrc) whose
adapter reports available. No engine name, port or path is held here.
Two snapshot types per poll, kept separate (spec): 'kv_cache' and 'queue'.

Metric meaning differs per engine and is never assumed equal:
    vLLM   kv_cache_usage_perc  fraction of KV blocks held by running requests
    SGLang token_usage          fraction of the KV token pool in use, including
                                radix cache retained after requests end
Missing readings are None (NULL), never 0.0 (MIC-1).
"""

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

METHOD_PREFIX = "kv_occupancy_"
# Poll period: ~/.alemsrc or shell ALEMS_SERVING_SNAPSHOT_INTERVAL_S, else the
# default. 0.5 s sees per request occupancy on short calls and costs one local
# /metrics GET per engine per poll. Stored per row in extra_json.
INTERVAL_ENV = "ALEMS_SERVING_SNAPSHOT_INTERVAL_S"
DEFAULT_INTERVAL_S = 0.5
# Upper bound to wait for the poller thread at stop: two poll periods.
JOIN_PERIODS = 2.0


def interval_s_default():
    # type: () -> float
    """Configured poll period in seconds; default when unset or invalid."""
    raw = os.environ.get(INTERVAL_ENV)
    try:
        return float(raw) if raw else DEFAULT_INTERVAL_S
    except ValueError:
        logger.warning("serving snapshots: bad %s=%r, using %s", INTERVAL_ENV, raw, DEFAULT_INTERVAL_S)
        return DEFAULT_INTERVAL_S


def local_engine_configs():
    # type: () -> List[Dict[str, Any]]
    """Resolved configs of all local serving engines (shared resolver)."""
    from core.serving.engine_config import local_engines
    return local_engines()


def find_listener(port):
    """Process listening on port, or None (shared with engine provenance)."""
    from core.serving.engine_provenance import find_listener as _find
    return _find(port)


class ServingSnapshotSampler:
    """Background poller of KV and queue state; buffer only, no DB access."""

    def __init__(self, interval_s=None, configs=None):
        # type: (float, Optional[List[Dict[str, Any]]]) -> None
        """
        Args:
            interval_s: poll period in seconds; None reads the configured value.
            configs:    serving_engine dicts; default from the models config.
        """
        self._interval = interval_s if interval_s is not None else interval_s_default()
        self._configs = configs
        self._adapters = []  # type: List[Any]
        self._rows = []  # type: List[Dict[str, Any]]
        self._stop = threading.Event()
        self._thread = None  # type: Optional[threading.Thread]

    def start(self):
        # type: () -> None
        """Build adapters for answering engines and start polling."""
        self._adapters = self._build_adapters()
        if not self._adapters:
            return   # no local engine configured or answering: nothing to sample
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="serving-snapshots", daemon=True)
        self._thread.start()

    def stop(self):
        # type: () -> List[Dict[str, Any]]
        """Stop polling, take one final reading, return the buffered rows."""
        if self._thread is not None:
            self._stop.set()
            self._thread.join(timeout=JOIN_PERIODS * self._interval + 1.0)
            self._poll_once()   # state at window end, for runs shorter than one interval
        rows, self._rows, self._thread = self._rows, [], None
        return rows

    def _build_adapters(self):
        # type: () -> List[Any]
        """Adapters for configured local engines that report available."""
        from core.serving.registry import ServingEngineRegistry
        adapters = []
        for cfg in (self._configs if self._configs is not None else local_engine_configs()):
            try:
                if not cfg.get("port") or find_listener(cfg["port"]) is None:
                    continue   # engine not running on this host now: nothing to poll, no warning
                adapter = ServingEngineRegistry.from_config(
                    {"type": cfg.get("type"), "endpoint": cfg.get("endpoint")})
                # The registry falls back to RemoteAPIAdapter when an engine is unreachable;
                # that stand in has no engine metrics and must never be polled as this engine.
                if adapter.is_available() and getattr(adapter, "ENGINE_TYPE", None) == cfg.get("type"):
                    adapters.append((cfg, adapter))
            except Exception as exc:  # one engine failing must not stop the others
                logger.warning("serving snapshots: %s not usable: %s", cfg.get("type"), exc)
        return adapters

    def _loop(self):
        # type: () -> None
        """Poll until stopped; first poll immediately at window start."""
        while not self._stop.is_set():
            self._poll_once()
            self._stop.wait(self._interval)

    def _poll_once(self):
        # type: () -> None
        """One kv_cache row and one queue row per engine."""
        for cfg, adapter in self._adapters:
            cfg["_interval_s"] = self._interval   # recorded with every row
            ts = time.time_ns()
            try:
                cache, queue = adapter.get_cache_state(), adapter.get_queue_state()
            except Exception as exc:
                logger.warning("serving snapshots: %s read failed: %s", cfg.get("type"), exc)
                continue
            self._rows.append(_kv_row(cfg, ts, cache))
            self._rows.append(_queue_row(cfg, ts, queue))


def _base_row(cfg, ts, snapshot_type):
    # type: (Dict[str, Any], int, str) -> Dict[str, Any]
    """Columns common to both snapshot types."""
    return {"timestamp_ns": ts, "engine_name": cfg.get("provider"),
            "engine_type": cfg.get("type"), "snapshot_type": snapshot_type,
            "telemetry_scope": "interval",
            "extra_json": json.dumps({"endpoint": cfg.get("endpoint"),
                                      "method_id": METHOD_PREFIX + str(cfg.get("type")),
                                      "interval_s": cfg.get("_interval_s")})}


def _kv_row(cfg, ts, cache):
    # type: (Dict[str, Any], int, Any) -> Dict[str, Any]
    """kv_cache row; fields the engine does not report stay None."""
    row = _base_row(cfg, ts, "kv_cache")
    row.update({"kv_capacity_tokens": getattr(cache, "capacity_tokens", None),
                "kv_occupied_tokens": getattr(cache, "occupied_tokens", None),
                "kv_occupancy_fraction": getattr(cache, "occupancy_fraction", None),
                "kv_hit_rate_aggregate": getattr(cache, "hit_rate_aggregate", None),
                "kv_num_evictions": getattr(cache, "num_evictions", None)})
    return row


def _queue_row(cfg, ts, queue):
    # type: (Dict[str, Any], int, Any) -> Dict[str, Any]
    """queue row: running and waiting requests, kept apart from KV use."""
    row = _base_row(cfg, ts, "queue")
    row.update({"queue_active": getattr(queue, "active", None),
                "queue_waiting": getattr(queue, "waiting", None),
                "queue_completed": getattr(queue, "completed", None),
                "queue_rejected": getattr(queue, "rejected", None)})
    return row
