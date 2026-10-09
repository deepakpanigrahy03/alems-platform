"""
BFCL tool provider (TOOL_PROVIDER_TYPE "bfcl").

Exposes the vendored BFCL backend classes of one task as A-LEMS tools.
A provider instance holds one BFCLEnv, so one instance serves one goal
execution; the agent adapter loads a task with load_task() before use.

Side effects are in memory only (C-TOOL side_effects "write" on an
in memory world): no file, network, or store I/O happens in execute(),
so the call is legal inside a measurement window (master 5.2a).

Recovery support (design 4.1):
    full restart  -> restore_initial()
    shallow resume -> restore_before(k), k = ordinal of the failed call
Every tool call snapshots first; the snapshot cost is reported by the
unit test that times it (S1 handover), never measured inside a window.

Turn bookkeeping: begin_turn(i) marks where turn i starts in the call
log, so the scorer receives calls grouped by user turn.
"""

import hashlib
import json
import time
from typing import Any, Dict, List

from alems_sdk.tools import (
    ToolDefinition,
    ToolExecutionContext,
    ToolProviderABC,
    ToolResult,
)

from .env import ERROR_PREFIX, BFCLEnv
from .tasks import CATEGORIES, load_entry, load_func_docs

# BFCL doc types are Python flavoured; JSON Schema needs these names.
_TYPE_MAP = {"dict": "object", "float": "number", "tuple": "array",
             "list": "array", "str": "string", "int": "integer", "bool": "boolean"}


def to_json_schema(node: Any) -> Any:
    """
    Convert a BFCL parameter doc into JSON Schema, recursively.

    "any" has no JSON Schema type, so the type key is dropped there.
    """
    if isinstance(node, list):
        return [to_json_schema(n) for n in node]
    if not isinstance(node, dict):
        return node
    out = {k: to_json_schema(v) for k, v in node.items() if k != "type"}
    t = node.get("type")
    if t is not None and t != "any":
        out["type"] = _TYPE_MAP.get(t, t)
    return out


def _sha(text: str) -> str:
    """Short payload hash for orchestration_events (first 16 hex)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class BFCLToolProvider(ToolProviderABC):
    """Stateful BFCL tools for one task; see module docstring."""

    TOOL_PROVIDER_TYPE = "bfcl"

    def __init__(self) -> None:
        """No required args (SPEC 35H); a task is loaded with load_task()."""
        super().__init__()
        self.env = None          # BFCLEnv once a task is loaded
        self.turn_starts: List[int] = []

    # ── task and turn lifecycle (called in prepare, outside windows) ──
    def load_task(self, task_id: str) -> None:
        """Load one BFCL entry and build its initial state."""
        self.env = BFCLEnv(load_entry(task_id))
        self.turn_starts = []

    def begin_turn(self, turn_index: int) -> None:
        """
        Mark the start of user turn turn_index (0 based, in order).

        Raises:
            ValueError: when turns are not opened in order.
        """
        if turn_index != len(self.turn_starts):
            raise ValueError("turn %d opened, expected %d"
                             % (turn_index, len(self.turn_starts)))
        self.turn_starts.append(self.env.calls_made)

    def restore_initial(self) -> None:
        """Full restart: initial state, empty call log and turns."""
        self.env.restore_initial()
        self.turn_starts = []

    def restore_before(self, k: int) -> None:
        """Shallow resume: state before call k; later turns forgotten."""
        self.env.restore_before(k)
        # Turns that started after the restored point no longer exist.
        self.turn_starts = [s for s in self.turn_starts if s <= k - 1]

    def state_hash(self, public_only: bool = False) -> str:
        """Hash of the current tool state (design 5.2 item 3)."""
        return self.env.state_hash(public_only=public_only)

    def calls_by_turn(self) -> List[List[str]]:
        """Executed call strings grouped by user turn."""
        log = self.env.call_log
        bounds = self.turn_starts + [len(log)]
        return [log[bounds[i]:bounds[i + 1]] for i in range(len(self.turn_starts))]

    def scorer_output(self) -> str:
        """Canonical JSON the bfcl_state scorer takes as its actual input."""
        return json.dumps({"task_id": self.env.entry["id"],
                           "turns": self.calls_by_turn()},
                          sort_keys=True, separators=(",", ":"))

    # ── ToolProviderABC ───────────────────────────────────────────────
    def execute(self, tool_name: str, arguments: Dict[str, Any],
                context: ToolExecutionContext) -> ToolResult:
        """Run one BFCL call. Never raises (ToolProviderABC contract)."""
        start = time.perf_counter_ns()
        if self.env is None:
            return ToolResult(success=False, result=None, tool_name=tool_name,
                              duration_ns=0, error="bfcl: no task loaded")
        args_text = json.dumps(arguments or {}, sort_keys=True, default=str)
        result = self.env.call(tool_name, [], dict(arguments or {}))
        failed = result.startswith(ERROR_PREFIX)
        return ToolResult(
            success=not failed, result=result, tool_name=tool_name,
            duration_ns=time.perf_counter_ns() - start,
            input_payload_hash=_sha(args_text), output_payload_hash=_sha(result),
            error=result if failed else "",
        )

    def get_tools(self) -> List[ToolDefinition]:
        """
        Tool definitions for the loaded task's classes, minus the task's
        excluded functions; all vendored classes when no task is loaded.
        """
        if self.env is None:
            from .env import CLASS_MODULES
            classes, excluded = list(CLASS_MODULES), set()
        else:
            classes = self.env.entry["involved_classes"]
            excluded = set(self.env.entry.get("excluded_function", []))
        docs = [d for c in classes for d in load_func_docs(c)]
        return [ToolDefinition(name=d["name"], description=d["description"],
                               parameters=to_json_schema(d["parameters"]))
                for d in docs if d["name"] not in excluded]

    def get_name(self) -> str:
        return "bfcl"

    def is_available(self) -> bool:
        """Available when the vendored data loads (mpmath is installed)."""
        try:
            import mpmath  # noqa: F401  (MathAPI dependency)
            return bool(CATEGORIES)
        except ImportError:
            return False
