"""
State grounded resume (SG) notice, RAF Addendum C (coordinator Oct 15).

SG resumes like SR (same restore, same history, same cache option); only the
notice differs. It states facts read from the tool environment after the
restore: the public state of every instance the failed turn touched, and,
for a tool failure, that the failed call did not take effect.

Never shown: ground truth, expected state, checker output. Built in the pre
attempt hook, outside the measurement window; its tokens count in attempt 2
because the notice is part of the attempt 2 prompt.
"""

import json
from typing import Any, Dict, List, Optional

from .env import _canon

MAX_STATE_CHARS = 6000      # cap on the rendered state; truncation is stated


def touched(env: Any, start: int) -> List[str]:
    """Instance names whose methods were called since the turn opened."""
    owner = {}
    for name, method in env.method_map().items():
        inst = getattr(method, "__self__", None)
        for cls, obj in env.instances.items():
            if obj is inst:
                owner[name] = cls
    out: List[str] = []
    for call in env.call_log[start:]:
        cls = owner.get(call.split("(", 1)[0])
        if cls and cls not in out:
            out.append(cls)
    return out


def public_state(env: Any, classes: List[str]) -> Dict[str, Any]:
    """Public attributes (no leading underscore) of the given instances."""
    seen: Dict[int, str] = {}
    out = {}
    for cls in classes:
        attrs = vars(env.instances[cls])
        out[cls] = {k: _canon(attrs[k], cls + "." + k, seen)
                    for k in sorted(attrs) if not k.startswith("_")}
    return out


def build_notice(env: Any, start: int, failure: Dict[str, Any]) -> str:
    """
    The SG notice for one recovery.

    Args:
        env: the BFCL environment after the SR restore (the actual state).
        start: calls executed before the failed turn opened.
        failure: the recorded failure point (kind, tool_name).
    """
    classes = touched(env, start)
    lines = ["[state] Facts about the tool state right now, read from the environment."]
    if classes:
        blob = json.dumps(public_state(env, classes), sort_keys=True,
                          separators=(",", ":"), default=str)
        if len(blob) > MAX_STATE_CHARS:
            blob = blob[:MAX_STATE_CHARS] + " ... (truncated)"
        lines.append("Current state of the tools used in this turn: " + blob)
    else:
        lines.append("No tool was called in this turn before the failure.")
    if failure.get("kind") == "tool" and failure.get("tool_name"):
        lines.append("The call %s failed and did not take effect." % failure["tool_name"])
    lines.append("Continue the task from this state.")
    return "\n".join(lines)
