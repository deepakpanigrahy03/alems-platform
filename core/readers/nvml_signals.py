"""
core/readers/nvml_signals.py — NVIDIA GPU thermal-response signals through NVML.

Independent of the GPU energy backend (DCGM, NVML or none), so the same
signals are read on every machine with a working NVIDIA driver:

  nvml:gpuN:clocks_event_reasons     raw bitmask of active clock-event
                                     (throttle) reasons; several can be active
                                     at once; decoded with the bindings' own
                                     constants (decoder kept in the catalog)
  nvml:gpuN:reason_ns:<name>         cumulative time per reason, nanoseconds
                                     (NVML field values); raw running totals,
                                     deltas are computed in analysis; reasons
                                     overlap, so they are never added together
  nvml:gpuN:temp_margin_c            temperature margin to the slowdown point
                                     (nvidia-smi "T.Limit")
  nvml:driver_version, nvml:version, read once per measurement (settings)
  nvml:gpuN:uuid

Every read is best effort: a field the driver or bindings do not offer is not
described; a failed read is None with its status, never 0.
"""
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# (short name, NVML field constant); only constants present in the bindings are used
_COUNTER_FIELDS = (
    ("sw_power_cap", "NVML_FI_DEV_CLOCKS_EVENT_REASON_SW_POWER_CAP"),
    ("hw_slowdown", "NVML_FI_DEV_CLOCKS_EVENT_REASON_HW_SLOWDOWN"),
    ("sync_boost", "NVML_FI_DEV_CLOCKS_EVENT_REASON_SYNC_BOOST"),
    ("sw_thermal_slowdown", "NVML_FI_DEV_CLOCKS_EVENT_REASON_SW_THERM_SLOWDOWN"),
    ("hw_thermal_slowdown", "NVML_FI_DEV_CLOCKS_EVENT_REASON_HW_THERM_SLOWDOWN"),
    ("hw_power_brake", "NVML_FI_DEV_CLOCKS_EVENT_REASON_HW_POWER_BRAKE_SLOWDOWN"),
)
_REASON_PREFIXES = ("nvmlClocksEventReason", "nvmlClocksThrottleReason")
_READ_BY = "NvmlSignals"


def _text(value):
    # type: (Any) -> str
    return value.decode() if isinstance(value, bytes) else str(value)


class NvmlSignals(object):
    """Describe and read NVIDIA thermal-response signals (SDK signal contract)."""

    def __init__(self):
        # type: () -> None
        self.ok = False
        self._p = None
        self._handles = []          # type: List[Any]
        self._uuids = []            # type: List[str]
        self._names = []            # type: List[str]
        self._settings = {}         # type: Dict[str, Optional[str]]
        self._fields = []           # type: List[Tuple[str, int]]
        try:
            import pynvml
            pynvml.nvmlInit()
            self._p = pynvml
            for i in range(pynvml.nvmlDeviceGetCount()):
                h = pynvml.nvmlDeviceGetHandleByIndex(i)
                self._handles.append(h)
                self._uuids.append(_text(pynvml.nvmlDeviceGetUUID(h)))
                self._names.append(_text(pynvml.nvmlDeviceGetName(h)))
            self._settings = {"nvml:driver_version": self._call_text("nvmlSystemGetDriverVersion"),
                              "nvml:version": self._call_text("nvmlSystemGetNVMLVersion")}
            self._fields = [(n, getattr(pynvml, c)) for n, c in _COUNTER_FIELDS if hasattr(pynvml, c)]
            self.ok = bool(self._handles)
        except Exception as exc:
            logger.info("NVML signals unavailable: %s", exc)

    def _call_text(self, fn):
        # type: (str) -> Optional[str]
        try:
            return _text(getattr(self._p, fn)())
        except Exception:
            return None

    def get_name(self):
        # type: () -> str
        return _READ_BY

    # ── catalog ──────────────────────────────────────────────────────────

    def decoder(self):
        # type: () -> Dict[str, str]
        """{bit value as text: reason name} from the bindings' constants."""
        out = {}
        for name in dir(self._p or object()):
            if not name.startswith(_REASON_PREFIXES) or name.endswith(("All", "None")):
                continue
            val = getattr(self._p, name)
            if isinstance(val, int) and val > 0 and (val & (val - 1)) == 0:
                out.setdefault(str(val), name.split("Reason", 1)[1])
        return dict(sorted(out.items(), key=lambda kv: int(kv[0])))

    def _reasons_fn(self):
        return (getattr(self._p, "nvmlDeviceGetCurrentClocksEventReasons", None)
                or getattr(self._p, "nvmlDeviceGetCurrentClocksThrottleReasons", None))

    def describe_signals(self):
        # type: () -> List[Dict]
        """Signal descriptors for every GPU; only what the bindings and driver offer."""
        if not self.ok:
            return []
        from alems_sdk.thermal_catalog import signal_descriptor as s
        ver = "nvml %s, driver %s" % (self._settings.get("nvml:version"),
                                      self._settings.get("nvml:driver_version"))
        out = [s(k, "setting", "nvml", k.split(":", 1)[1], "text", "validated", True, _READ_BY,
                 notes="read once per measurement") for k in sorted(self._settings)]
        for i, uuid in enumerate(self._uuids):
            g, dev = "nvml:gpu%d" % i, "%s %s" % (self._names[i], uuid)
            out.append(s(g + ":uuid", "setting", "nvml", "nvmlDeviceGetUUID", "text", "validated", True,
                         _READ_BY, device=dev, scope="gpu"))
            if self._reasons_fn() is not None:
                out.append(s(g + ":clocks_event_reasons", "bitmask", "nvml",
                             "nvmlDeviceGetCurrentClocksEventReasons", "bitmask", "validated", True,
                             _READ_BY, decoder=self.decoder(), decoder_version=ver, device=dev,
                             scope="gpu", notes="several reasons can be active at once; unknown bits "
                                                "are kept in the raw value and not named"))
            for name, _fid in self._fields:
                out.append(s("%s:reason_ns:%s" % (g, name), "counter", "nvml", "field " + name, "ns",
                             "validated", True, _READ_BY, device=dev, scope="gpu",
                             notes="cumulative; deltas within one counter epoch only; reasons "
                                   "overlap and are never summed"))
            if getattr(self._p, "nvmlDeviceGetMarginTemperature", None) is not None:
                out.append(s(g + ":temp_margin_c", "level", "nvml", "nvmlDeviceGetMarginTemperature",
                             "C", "empirical", True, _READ_BY, device=dev, scope="gpu",
                             notes="margin to the slowdown point (nvidia-smi T.Limit)"))
        return out

    # ── reads ────────────────────────────────────────────────────────────

    def read_signals(self):
        # type: () -> Dict[str, Dict]
        """{signal id: {"value", "unit", "status"}}; a failed read is None, never 0."""
        if not self.ok:
            return {}
        out = {k: _v(v, "text") for k, v in self._settings.items()}
        reasons = self._reasons_fn()
        margin = getattr(self._p, "nvmlDeviceGetMarginTemperature", None)
        for i, h in enumerate(self._handles):
            g = "nvml:gpu%d" % i
            out[g + ":uuid"] = _v(self._uuids[i], "text")
            if reasons is not None:
                out[g + ":clocks_event_reasons"] = _v(_try(lambda: int(reasons(h))), "bitmask")
            out.update(self._counters(g, h))
            if margin is not None:
                out[g + ":temp_margin_c"] = _v(_try(lambda: _margin(margin(h))), "C")
        return out

    def _counters(self, g, h):
        # type: (str, Any) -> Dict[str, Dict]
        if not self._fields:
            return {}
        ids = ["%s:reason_ns:%s" % (g, n) for n, _ in self._fields]
        try:
            vals = self._p.nvmlDeviceGetFieldValues(h, [fid for _, fid in self._fields])
        except Exception:
            return {k: _v(None, "ns", "READ_FAILED") for k in ids}
        out = {}
        for k, fv in zip(ids, vals):
            ok = getattr(fv, "nvmlReturn", 1) == 0
            out[k] = _v(int(fv.value.ullVal) if ok else None, "ns", None if ok else "READ_FAILED")
        return out


def _try(fn):
    try:
        return fn()
    except Exception:
        return None


def _margin(r):
    """Margin as a number: the bindings return an int or a struct, depending on version."""
    if isinstance(r, (int, float)):
        return r
    for attr in ("margin", "marginTemperature", "value"):
        if hasattr(r, attr):
            return getattr(r, attr)
    return None


def _v(value, unit, status=None):
    # type: (Any, str, Optional[str]) -> Dict[str, Any]
    return {"value": value, "unit": unit,
            "status": status or ("VALID" if value is not None else "READ_FAILED")}
