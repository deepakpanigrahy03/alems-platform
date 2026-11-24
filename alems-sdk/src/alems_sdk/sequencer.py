"""
alems_sdk.sequencer: contract for step kinds run by the A-LEMS sequencer.

A step kind performs one kind of controlled activity (for example a steady
copy at a known size, a kernel read pass) as a sequencer step. Plugins
register under the entry point group alems.sequencer.steps and import only
alems_sdk. Core owns timing, the measurement window, cancellation, attempts
and persistence; a kind never touches the store.

Lifecycle: validate (plan time); prepare (before the bracket's pre idle);
then per attempt is_available (dispatch time) -> setup -> measure (inside the
core measurement window, stored as a normal run) -> teardown -> evaluate.
measure() raises StepFailed(code, detail) when the work did not complete.
Every method receives `step` = the step's parameters from the plan (dict) and
`ctx` = an SDK only context: attempt_id, cancelled() (True when asked to stop).
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, NamedTuple, Optional, Tuple


class Availability(NamedTuple):
    """Per request availability: ok, a stable reason code, and detail text."""
    ok: bool
    code: str = "ok"
    detail: str = ""


class StepFailed(Exception):
    """Raised by measure() when the step did not complete; code is a stable reason code."""

    def __init__(self, code, detail=""):
        # type: (str, str) -> None
        super().__init__(detail or code)
        self.code = code


class StepKindABC(ABC):
    """
    Base class of a sequencer step kind.

    PARAMS declares accepted parameters, checked before any step runs:
        {"size_mib": {"type": "number", "unit": "MiB", "required": True},
         "direction": {"type": "string", "choices": ["h2d", "d2h"], "required": True}}
    """

    KIND = ""                       # name used in plans, e.g. "copy_rate"
    MEASUREMENT = "measured"        # "profiling": measure() runs outside the measured path (no run, no energy)
    PARAMS = {}  # type: Dict[str, Dict[str, Any]]
    ALEMS_PLUGIN_META = {}  # type: Dict[str, Any]

    @classmethod
    def validate(cls, params):
        # type: (Dict[str, Any]) -> List[str]
        """Errors in the step parameters (empty when valid); never raises."""
        errors = []
        for name, spec in cls.PARAMS.items():
            if name not in params:
                if spec.get("required"):
                    errors.append("%s: missing parameter %s" % (cls.KIND, name))
                continue
            value = params[name]
            if spec.get("type") == "number" and not isinstance(value, (int, float)):
                errors.append("%s: %s must be a number" % (cls.KIND, name))
            if spec.get("choices") and value not in spec["choices"]:
                errors.append("%s: %s=%r not in %s" % (cls.KIND, name, value, spec["choices"]))
        return errors

    @abstractmethod
    def is_available(self, step, ctx):
        # type: (Dict[str, Any], Dict[str, Any]) -> Availability
        """Whether this request can run here now (device, direction, size); never raises."""

    def prepare(self, step, ctx):
        # type: (Dict[str, Any], Dict[str, Any]) -> Any
        """
        Optional, before the bracket's pre idle and gate (for example a probe that
        sizes the step). The return value reaches setup, measure and evaluate as
        ctx["prepared"].
        """
        return None

    def setup(self, step, ctx):
        # type: (Dict[str, Any], Dict[str, Any]) -> None
        """Preparation outside the measured interval (allocation, warm up)."""

    @abstractmethod
    def measure(self, step, ctx):
        # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Tuple[Any, Optional[str]]]
        """
        The measured activity. Returns results as {key: (value, unit)}, for
        example {"known_bytes": (268435456, "B"), "achieved_rate": (21.3, "GB/s")}.
        ctx["cancelled"]() is True when the sequencer asks the step to stop.
        """

    def evaluate(self, step, ctx, results, run):
        # type: (Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]) -> Dict[str, Tuple[Any, Optional[str]]]
        """
        Optional checks after the run is stored. `run` is the run's in memory
        data (plain dicts and lists: samples, traffic results); results of
        measure() are passed in. Returns extra results {key: (value, unit)}.
        """
        return {}

    def teardown(self, step, ctx):
        # type: (Dict[str, Any], Dict[str, Any]) -> None
        """Release resources outside the measured interval."""
