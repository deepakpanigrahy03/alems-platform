"""
alems_sdk.tool_latency: config driven hold of a tool result before the agent sees it.

Used by every tool path (the core agent's tool execution and plugin agents such
as BFCL) so one experiment setting applies identically everywhere.

Lifecycle: the experiment config loader calls configure() once per profile with
the `tool_latency` section (or None), so a setting never leaks into the next
profile run in the same process. Tool paths call hold() right after the tool
returns and before the tool call window closes, so the added wait is part of the
measured tool call. hold() returns the fields to record on the tool event.

Every value comes from the config section; this module has no defaults. An
invalid section raises ValueError at configure() time, before any run starts.

Section (experiment YAML):
    tool_latency:
      enabled: true
      delay_s: 2.0          # added wait after the tool returns
      wait_mode: sleep      # sleep (blocking) | poll (check the clock every poll_ms)
      poll_ms: 10           # required for poll; 0 = busy spin
      apply_to: success     # success | all (all also delays failed tool calls)
"""
import time
from typing import Any, Dict, Optional

WAIT_MODES = ("sleep", "poll")
APPLY_TO = ("success", "all")
_ACTIVE = None  # type: Optional[Dict[str, Any]]


def validate(section):
    # type: (Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]
    """Checked copy of the section, or None when absent or disabled; raises ValueError."""
    if not section or not section.get("enabled", False):
        return None
    missing = [k for k in ("delay_s", "wait_mode", "apply_to") if k not in section]
    if missing:
        raise ValueError("tool_latency: missing %s" % ", ".join(missing))
    delay = float(section["delay_s"])
    if delay < 0:
        raise ValueError("tool_latency: delay_s must be >= 0")
    mode, apply_to = section["wait_mode"], section["apply_to"]
    if mode not in WAIT_MODES or apply_to not in APPLY_TO:
        raise ValueError("tool_latency: wait_mode in %s, apply_to in %s" % (WAIT_MODES, APPLY_TO))
    if mode == "poll" and "poll_ms" not in section:
        raise ValueError("tool_latency: poll_ms is required for wait_mode poll")
    poll_ms = float(section["poll_ms"]) if mode == "poll" else None
    if poll_ms is not None and poll_ms < 0:
        raise ValueError("tool_latency: poll_ms must be >= 0")
    return {"delay_s": delay, "wait_mode": mode, "poll_ms": poll_ms, "apply_to": apply_to}


def configure(section):
    # type: (Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]
    """Set (or clear) the active setting for this process; returns it."""
    global _ACTIVE
    _ACTIVE = validate(section)
    return _ACTIVE


def active():
    # type: () -> Optional[Dict[str, Any]]
    """The active setting, or None."""
    return _ACTIVE


def _wait(cfg):
    # type: (Dict[str, Any]) -> None
    """Wait delay_s by one blocking sleep, or by polling the clock every poll_ms."""
    if cfg["wait_mode"] == "sleep":
        time.sleep(cfg["delay_s"])
        return
    end = time.monotonic() + cfg["delay_s"]
    step = cfg["poll_ms"] / 1000.0
    while time.monotonic() < end:
        if step > 0:
            time.sleep(step)  # poll interval; 0 means a busy spin that keeps a core active


def hold(success=True):
    # type: (bool) -> Dict[str, Any]
    """
    Apply the active setting to one tool result.

    Returns the fields to add to the tool event: injected_delay_ms (measured
    wall time actually waited), tool_wait_mode. Empty dict when no setting is
    active or the setting does not apply to this result.
    """
    cfg = _ACTIVE
    if cfg is None or (cfg["apply_to"] == "success" and not success):
        return {}
    t0 = time.monotonic()
    _wait(cfg)
    return {"injected_delay_ms": round((time.monotonic() - t0) * 1000.0, 3),
            "tool_wait_mode": cfg["wait_mode"]}
