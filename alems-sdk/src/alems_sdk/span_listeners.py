"""
alems_sdk.span_listeners: contract for listeners notified when spans open and close.

A listener mirrors A-LEMS spans into another tool (for example NVTX ranges for
NVIDIA profilers). Plugins register under the entry point group
alems.spans.listeners and import only alems_sdk.

Calls happen inside the measurement window: they must be cheap, never block,
never touch the store, and never raise (core logs and ignores exceptions).
The span dict has: span_id, trace_id, parent_span_id, kind, name, start_ns,
end_ns (None on open), status. Times are the process monotonic clock
(time.perf_counter_ns), as stored in the spans table.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict


class SpanListenerABC(ABC):
    """Receives span open and close events."""

    ALEMS_PLUGIN_META = {}  # type: Dict[str, Any]
    CONFIG_KEY = ""   # section under plugins: in app_settings.yaml, e.g. "nvtx"

    def configure(self, config):
        # type: (Dict[str, Any]) -> None
        """Resolved settings from core (app_settings plugins.<CONFIG_KEY>, then ALEMS_<KEY>_<FIELD>)."""
        self.config = dict(config or {})

    @abstractmethod
    def enabled(self):
        # type: () -> bool
        """True when this listener should receive events (configuration and dependencies present)."""

    @abstractmethod
    def on_open(self, span):
        # type: (Dict[str, Any]) -> None
        """A span opened."""

    @abstractmethod
    def on_close(self, span):
        # type: (Dict[str, Any]) -> None
        """A span closed (end_ns and status set)."""
