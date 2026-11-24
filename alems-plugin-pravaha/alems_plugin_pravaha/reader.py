"""
alems_plugin_pravaha.reader: GPU memory traffic in bytes via pravaha-mem.

Author: Deepak Panigrahy

What it measures: five GPU L2 counters sampled by CUPTI PM sampling in a
separate reader process (pravaha_mem.observe): copy engine bytes, kernel read
miss bytes, kernel write bytes, all read miss bytes, all write bytes. Device
wide (all processes on the GPU); on GB10 all reads plus all writes estimates
NVLink-C2C payload. Not a link meter. Totals include the source columns.

Configuration (never hardcoded; ~/.alemsrc or shell, shell wins):
    ALEMS_PRAVAHA_ENABLED      1 (default) or 0
    ALEMS_PRAVAHA_INTERVAL_MS  sample interval, default 10
    ALEMS_PRAVAHA_DEVICE       GPU index, default ALEMS_GPU_INDEX, else first GPU

Imports only alems_sdk and pravaha_mem (CH39-2). Never raises from
is_available(); start() raises the reader's error, which the core collector
records as status 'failed' with the reason.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

from alems_sdk.readers import TrafficReaderABC

logger = logging.getLogger(__name__)

# Byte columns pravaha reports per sample (pravaha-mem 1.0 API).
BYTE_COLUMNS = ("copy_bytes", "kernel_read_miss_bytes", "kernel_write_bytes",
                "all_read_miss_bytes", "all_write_bytes")
DEFAULT_INTERVAL_MS = 10


def _env_int(name, default):
    # type: (str, Optional[int]) -> Optional[int]
    """Integer from the environment; default when unset or invalid (logged)."""
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("pravaha reader: bad %s=%r, using %s", name, raw, default)
        return default


class PravahaTrafficReader(TrafficReaderABC):
    """Live GPU memory traffic samples for the energy window."""

    ALEMS_PLUGIN_META = {"plugin_id": "pravaha", "family": "measurement",
                         "extension_point": "alems.readers.traffic", "version": "1.0.0",
                         "sdk_range": ">=1.0,<2.0"}
    FIDELITY = "MEASURED"
    METHOD_ID = "gpu_l2_traffic_pravaha_v1"

    def __init__(self, config=None):
        # type: (Optional[Dict[str, Any]]) -> None
        self._cfg = dict(config or {})
        self._obs = None  # type: Any
        self._status = None  # type: Optional[Dict[str, Any]]
        self._reason = None  # type: Optional[str]
        self.interval_ms = _env_int("ALEMS_PRAVAHA_INTERVAL_MS", DEFAULT_INTERVAL_MS)
        self.device = _env_int("ALEMS_PRAVAHA_DEVICE", _env_int("ALEMS_GPU_INDEX", 0))

    def get_name(self):
        # type: () -> str
        return "pravaha"

    def is_available(self):
        # type: () -> bool
        """True when enabled and pravaha_mem.status() reports ready; never raises."""
        if _env_int("ALEMS_PRAVAHA_ENABLED", 1) == 0:
            self._reason = "disabled by ALEMS_PRAVAHA_ENABLED=0"
            return False
        try:
            import pravaha_mem
            self._status = pravaha_mem.status()
        except Exception as exc:  # not installed or reader missing
            self._reason = "pravaha_mem unavailable: %s" % exc
            return False
        ready = bool(self._status.get("ready"))
        self._reason = None if ready else "not ready: %s" % "; ".join(self._status.get("fix") or [])
        return ready

    def start(self):
        # type: () -> None
        """Start the separate reader process; raises its error on failure."""
        import pravaha_mem
        self._obs = pravaha_mem.observe(interval_ms=self.interval_ms, device=self.device).start()

    def stop(self):
        # type: () -> Dict[str, Any]
        """Stop sampling and return the run result (status measured)."""
        obs, self._obs = self._obs, None
        obs.stop()
        samples = [self._row(s) for s in self._window_samples(obs)]
        if not samples:
            return self._result("failed", "no_samples: reader returned no sample inside the window", [], obs)
        return self._result("measured", None, samples, obs)

    @staticmethod
    def _window_samples(obs):
        # type: (Any) -> List[Dict[str, Any]]
        """pravaha samples overlapping [t_start, t_end] as dicts with A-LEMS column names."""
        from pravaha_mem.observer import FIELDS, JSON_NAMES
        t0, t1 = obs.t_start, obs.t_end
        out = []
        for s in obs.samples:
            if t0 is not None and s.t1 <= t0 or t1 is not None and s.t0 >= t1:
                continue
            d = {JSON_NAMES[f]: getattr(s, f, None) for f in FIELDS}
            d.update(t_start_ns=s.t0, t_end_ns=s.t1)
            out.append(d)
        return out

    def unavailable_result(self):
        # type: () -> Dict[str, Any]
        return self._result("unavailable", self._reason, [], None)

    def failed_result(self, reason):
        # type: (str) -> Dict[str, Any]
        return self._result("failed", reason, [], None)

    def _row(self, s):
        # type: (Dict[str, Any]) -> Dict[str, Any]
        """One sample row; a column pravaha did not report stays None (MIC-1)."""
        row = {c: s.get(c) for c in BYTE_COLUMNS}
        row.update({"t_start_ns": s.get("t_start_ns"), "t_end_ns": s.get("t_end_ns"),
                    "phase": s.get("phase"), "measurement_scope": "device_wide",
                    "quality_flag": s.get("quality_flag") or s.get("quality")})
        if row["t_start_ns"] is not None and row["t_end_ns"] is not None:
            row["interval_ns"] = row["t_end_ns"] - row["t_start_ns"]
        return row

    def _result(self, status, reason, samples, obs):
        # type: (str, Optional[str], List[Dict[str, Any]], Any) -> Dict[str, Any]
        """Per run record plus samples, in the shape core persistence writes."""
        prov = (self._status or {}).get("provenance") or {}
        return {
            "reader": self.get_name(), "method_id": self.METHOD_ID, "status": status,
            "reason": reason, "pravaha_version": prov.get("pravaha"),
            "driver": prov.get("driver"), "cuda": prov.get("cuda"),
            "interval_ms": self.interval_ms, "device": self.device,
            "sample_count": len(samples),
            "baseline_json": _json(obs, "baseline"), "quality_json": _json(obs, "quality"),
            "samples": samples,
        }


def _json(obs, method):
    # type: (Any, str) -> Optional[str]
    """JSON of obs.baseline() or obs.quality(); None when absent (logged)."""
    if obs is None:
        return None
    try:
        return json.dumps(getattr(obs, method)(), default=str)
    except Exception as exc:
        logger.warning("pravaha reader: %s() unavailable: %s", method, exc)
        return None
