"""
core/sequencer/gates.py: thermal readiness gate before a measured step.

Why: on a chip that is still warm from the previous step, power and speed
differ; comparing a treatment that started hot with one that started cool
biases the result. The gate waits until the hottest selected sensor is at or
below a threshold, up to a maximum wait, and records what it saw.

Hardware independent: temperatures come from the platform thermal reader
(ThermalReaderABC.read_all_thermal(): sensor name to Celsius) through the
reader factory; no device is named here. No sensors gives outcome
'not_available' (recorded), never a fake temperature.

Configuration (plan `gate:` block, else ~/.alemsrc, else off):
    max_temp_c      ALEMS_SEQ_GATE_MAX_TEMP_C   (unset: gate off)
    sensor_pattern  ALEMS_SEQ_GATE_SENSOR_PATTERN (regex on sensor names; default all)
    max_wait_s      ALEMS_SEQ_GATE_MAX_WAIT_S   (default 600)
    poll_s          ALEMS_SEQ_GATE_POLL_S       (default 5)
"""

import logging
import os
import re
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)
_DEFAULTS = {"max_wait_s": 600.0, "poll_s": 5.0, "sensor_pattern": ".*"}
_ENV = {"max_temp_c": "ALEMS_SEQ_GATE_MAX_TEMP_C", "sensor_pattern": "ALEMS_SEQ_GATE_SENSOR_PATTERN",
        "max_wait_s": "ALEMS_SEQ_GATE_MAX_WAIT_S", "poll_s": "ALEMS_SEQ_GATE_POLL_S"}


def gate_config(plan_gate):
    # type: (Optional[Dict[str, Any]]) -> Dict[str, Any]
    """Plan values over environment values over defaults."""
    out = {}
    for key, env in _ENV.items():
        val = (plan_gate or {}).get(key, os.environ.get(env, _DEFAULTS.get(key)))
        out[key] = val
    for key in ("max_temp_c", "max_wait_s", "poll_s"):
        out[key] = float(out[key]) if out[key] not in (None, "") else None
    return out


def hottest(read_fn, pattern):
    # type: (Callable[[], Dict[str, float]], str) -> Optional[tuple]
    """(sensor, celsius) of the hottest matching sensor; None when none readable."""
    try:
        temps = read_fn() or {}
    except Exception as exc:  # reader failure is a recorded gate outcome
        logger.warning("thermal gate: read failed: %s", exc)
        return None
    rx = re.compile(pattern)
    hits = [(n, t) for n, t in temps.items() if t is not None and rx.search(n)]
    return max(hits, key=lambda x: x[1]) if hits else None


def wait_ready(read_fn, cfg, sleep=time.sleep, clock=time.monotonic):
    # type: (Callable[[], Dict[str, float]], Dict[str, Any], Callable, Callable) -> Dict[str, Any]
    """
    Wait until the hottest selected sensor is at or below max_temp_c.

    Returns the gate record: outcome ('off', 'passed', 'timeout',
    'not_available'), thresholds, first and last observation, waited seconds.
    """
    if cfg.get("max_temp_c") is None:
        return {"outcome": "off"}
    start = clock()
    first = last = hottest(read_fn, cfg["sensor_pattern"])
    while last is not None and last[1] > cfg["max_temp_c"] and clock() - start < cfg["max_wait_s"]:
        sleep(cfg["poll_s"])
        last = hottest(read_fn, cfg["sensor_pattern"])
    if last is None:
        outcome = "not_available"
    else:
        outcome = "passed" if last[1] <= cfg["max_temp_c"] else "timeout"
    return {"outcome": outcome, "max_temp_c": cfg["max_temp_c"], "sensor_pattern": cfg["sensor_pattern"],
            "max_wait_s": cfg["max_wait_s"], "first": first, "last": last,
            "waited_s": round(clock() - start, 3)}
