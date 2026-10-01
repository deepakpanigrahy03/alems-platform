"""
================================================================================
ADAPTERS BASE: runtime helpers plus compatibility path for adapter contracts
================================================================================

Since 39.5.1a the contracts TextGenABC and MediaABC live in alems_sdk.generation
(D2.1). This module re-exports the identical objects (D2.2, Rule S) and keeps
the runtime helper BaseAdapterMixin (psutil network counters, throughput math,
phase metrics dict), which is runtime behaviour and therefore not part of the
SDK. Core adapters that use the helpers inherit BaseAdapterMixin explicitly.

PAC rule: every adapter inherits one contract; never instantiated directly.
MPC rule: adapters do not write any store; they return dicts.
================================================================================
"""

import logging
from typing import Any, Dict, Optional

import psutil

from alems_sdk.generation import MediaABC, TextGenABC  # noqa: F401  (re-export)

logger = logging.getLogger(__name__)


class BaseAdapterMixin:
    """
    Runtime helpers shared by core adapters.

    Provides network counter reads, throughput calculation, and the standard
    phase_metrics dict builder. Never instantiated directly.
    """

    def _get_network_counters(self) -> Dict[str, int]:
        """
        Read an OS level network I/O counters snapshot.

        Used as a before and after pair to compute a per call delta.
        Returns zeros on failure; never raises (PAC graceful degradation).

        Returns:
            dict: bytes_sent, bytes_recv, tcp_retransmits
        """
        result = {"bytes_sent": 0, "bytes_recv": 0, "tcp_retransmits": 0}
        try:
            net = psutil.net_io_counters()
            result["bytes_sent"] = net.bytes_sent
            result["bytes_recv"] = net.bytes_recv
            # TCP retransmits from /proc/net/snmp: Linux only, skipped elsewhere.
            with open("/proc/net/snmp", "r") as f:
                for line in f:
                    if line.startswith("Tcp:"):
                        parts = line.split()
                        if "RetransSegs" in parts:
                            result["tcp_retransmits"] = int(
                                parts[parts.index("RetransSegs") + 1]
                            )
                        break
        except Exception as e:
            logger.debug("Network counter read failed: %s", e)
        return result

    def _network_delta(self, before: Dict, after: Dict) -> Dict[str, int]:
        """
        Per call network usage from two counter snapshots.

        Returns:
            dict: bytes_sent, bytes_recv, tcp_retransmits (deltas)
        """
        return {
            "bytes_sent": after["bytes_sent"] - before["bytes_sent"],
            "bytes_recv": after["bytes_recv"] - before["bytes_recv"],
            "tcp_retransmits": after["tcp_retransmits"] - before["tcp_retransmits"],
        }

    def _throughput_kbps(self, prompt_bytes: int, response_bytes: int, latency_ms: float) -> float:
        """
        Effective application throughput in kbps.

        Formula: (total_bytes * 8) / latency_seconds / 1000. Cloud calls pass
        non_local_ms, local calls total_ms.

        Returns:
            float kbps; 0.0 if latency_ms <= 0
        """
        if latency_ms <= 0:
            return 0.0
        return (prompt_bytes + response_bytes) * 8 / (latency_ms / 1000) / 1000

    def _make_phase_metrics(
        self,
        total_time_ms: float,
        preprocess_ms: float,
        non_local_ms: float,
        local_compute_ms: float,
        postprocess_ms: float,
        app_throughput_kbps: float,
        cpu_percent_during_wait: float,
        ttft_ms: Optional[float] = None,
        tpot_ms: Optional[float] = None,
        token_throughput: Optional[float] = None,
        streaming_enabled: int = 0,
        first_token_time_ns: Optional[int] = None,
        last_token_time_ns: Optional[int] = None,
        request_start_ns: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Build the standard phase_metrics dict consumed by the harness.

        Shape matches the historical _current_llm_metrics contract exactly.

        Returns:
            dict with the phase timing, throughput and streaming keys.
        """
        return {
            "total_time_ms": total_time_ms,
            "preprocess_ms": preprocess_ms,
            "non_local_ms": non_local_ms,
            "local_compute_ms": local_compute_ms,
            "postprocess_ms": postprocess_ms,
            "app_throughput_kbps": app_throughput_kbps,
            "cpu_percent_during_wait": cpu_percent_during_wait,
            # Streaming metrics stay NULL until streaming is implemented.
            "ttft_ms": ttft_ms,
            "tpot_ms": tpot_ms,
            "token_throughput": token_throughput,
            "streaming_enabled": streaming_enabled,
            "first_token_time_ns": first_token_time_ns,
            "last_token_time_ns": last_token_time_ns,
            "request_start_ns": request_start_ns,
        }


__all__ = ["BaseAdapterMixin", "TextGenABC", "MediaABC"]
