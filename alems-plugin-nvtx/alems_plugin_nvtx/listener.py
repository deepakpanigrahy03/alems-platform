"""
alems_plugin_nvtx.listener: one NVTX range per A-LEMS span.

Author: Deepak Panigrahy

Settings (resolved by core): app_settings.yaml plugins.nvtx {enabled, domain}, then
ALEMS_NVTX_ENABLED / ALEMS_NVTX_DOMAIN (~/.alemsrc or shell). Enabled only when
enabled is true and the nvtx package imports. Ranges use start_range/end_range (not
push/pop) because agent spans may interleave and cross threads. Message:
"<kind>:<name> [<span_id>]" so the agreement checker can match each range to its span.
"""

from typing import Any, Dict

from alems_sdk.span_listeners import SpanListenerABC


class NvtxSpanListener(SpanListenerABC):
    """Opens and closes NVTX ranges as spans open and close."""

    ALEMS_PLUGIN_META = {"plugin_id": "nvtx_spans", "family": "observability",
                         "extension_point": "alems.spans.listeners", "version": "1.0.0",
                         "sdk_range": ">=1.0,<2.0"}

    CONFIG_KEY = "nvtx"

    def __init__(self):
        self._nvtx = None  # type: Any
        self._ranges = {}  # type: Dict[str, Any]
        self.config = {}  # type: Dict[str, Any]

    def enabled(self):
        # type: () -> bool
        if not self.config.get("enabled"):
            return False
        try:
            import nvtx
        except ImportError:
            return False
        self._nvtx = nvtx
        return True

    def on_open(self, span):
        # type: (Dict[str, Any]) -> None
        msg = "%s:%s [%s]" % (span["kind"], span["name"], span["span_id"])
        self._ranges[span["span_id"]] = self._nvtx.start_range(message=msg, domain=self.config.get("domain"))

    def on_close(self, span):
        # type: (Dict[str, Any]) -> None
        handle = self._ranges.pop(span["span_id"], None)
        if handle is not None:
            self._nvtx.end_range(handle)
