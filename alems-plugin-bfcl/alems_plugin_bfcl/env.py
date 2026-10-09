"""
BFCL environment: the stateful tool world of one BFCL multi turn task.

One BFCLEnv holds the instances of the classes a task involves, built from
the task's initial_config exactly as upstream does (class constructor, then
_load_scenario on a deep copy of the class config). It executes calls by
method name, keeps a snapshot before every call, and restores either the
initial state (full restart) or the state before call k (shallow resume).

Calls are parsed with ast and dispatched as bound method calls. Upstream
uses eval on rewritten strings and module level globals; we keep upstream
result formatting and the forbidden name list so execution results stay
identical, without eval.

Imports: standard library and this package only (INV-14).
"""

import ast
import copy
import hashlib
import importlib
import inspect
import json
import random
from typing import Any, Dict, List, Optional, Tuple

# Class name to vendored module, same mapping as upstream
# constants/executable_backend_config.py (multi turn classes only).
CLASS_MODULES = {
    "GorillaFileSystem": "gorilla_file_system",
    "MathAPI": "math_api",
    "MessageAPI": "message_api",
    "TwitterAPI": "posting_api",
    "TicketAPI": "ticket_api",
    "TradingBot": "trading_bot",
    "TravelAPI": "travel_booking",
    "VehicleControlAPI": "vehicle_control",
}

# Upstream STATELESS_CLASSES: built without _load_scenario.
STATELESS_CLASSES = ("MathAPI",)

# Upstream refuses these names before eval; we refuse them before dispatch
# so a model that calls them sees the same error text as upstream.
FORBIDDEN_NAMES = ("kill", "exit", "quit", "remove", "unlink", "popen", "Popen", "run")

# Upstream prefix for any execution error; the scorer compares result
# strings, so the prefix must match exactly.
ERROR_PREFIX = "Error during execution: "

# Attributes whose value depends on wall clock time, not on the call
# sequence. Excluded from the full state hash so replays hash equally.
NONDETERMINISTIC_ATTRS = ("_last_modified",)


class CallParseError(ValueError):
    """Raised when a call string is not a single literal argument call."""


def _new_instance(class_name: str, config: dict, long_context: bool) -> Any:
    """
    Build one backend instance the way upstream does.

    Args:
        class_name: a key of CLASS_MODULES.
        config: the task's full initial_config.
        long_context: True for the long_context category.

    Returns:
        The loaded instance.
    """
    module = importlib.import_module(
        "alems_plugin_bfcl.upstream." + CLASS_MODULES[class_name])
    instance = getattr(module, class_name)()
    if class_name in STATELESS_CLASSES:
        return instance
    # Deep copy so the entry dict is never mutated by the backend.
    instance._load_scenario(copy.deepcopy(config.get(class_name, {})),
                            long_context=long_context)
    return instance


def _format_result(value: Any) -> str:
    """Upstream result formatting: str as is, dict as JSON, else str()."""
    if type(value) == str:  # noqa: E721  (upstream uses exact type)
        return value
    if type(value) == dict:  # noqa: E721
        try:
            return json.dumps(value)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def parse_call(call: str) -> Tuple[str, list, dict]:
    """
    Parse "name(arg, key=value)" into (name, args, kwargs).

    Only literal arguments are accepted (ast.literal_eval), which covers
    every BFCL ground truth call; anything else raises CallParseError.

    Args:
        call: one call string.

    Returns:
        (function name, positional args, keyword args)
    """
    try:
        node = ast.parse(call.strip(), mode="eval").body
    except SyntaxError as exc:
        raise CallParseError("unparseable call: %s" % exc)
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
        raise CallParseError("not a plain function call")
    try:
        args = [ast.literal_eval(a) for a in node.args]
        kwargs = {k.arg: ast.literal_eval(k.value) for k in node.keywords}
    except ValueError as exc:
        raise CallParseError("non literal argument: %s" % exc)
    if None in kwargs:
        raise CallParseError("**kwargs expansion not allowed")
    return node.func.id, args, kwargs


def format_call(name: str, args: list, kwargs: dict) -> str:
    """Canonical call string; parse_call(format_call(...)) round trips."""
    parts = [repr(a) for a in args]
    parts += ["%s=%r" % (k, kwargs[k]) for k in sorted(kwargs)]
    return "%s(%s)" % (name, ",".join(parts))


def _canon(value: Any, path: str, seen: Dict[int, str]) -> Any:
    """
    Convert a backend value into canonical JSON data.

    Objects are walked through vars(); an object met a second time (shared
    reference or cycle, for example Directory.parent) becomes a reference
    to the path where it was first met. Traversal order is sorted, so the
    result is deterministic.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, random.Random):
        # The generator state decides every later draw, so it is state.
        return {"__random__": repr(value.getstate())}
    if isinstance(value, (list, tuple)):
        return [_canon(v, "%s[%d]" % (path, i), seen) for i, v in enumerate(value)]
    if isinstance(value, (set, frozenset)):
        return sorted(repr(v) for v in value)
    if isinstance(value, dict):
        return {str(k): _canon(value[k], "%s.%s" % (path, k), seen)
                for k in sorted(value, key=str)}
    return _canon_object(value, path, seen)


def _canon_object(obj: Any, path: str, seen: Dict[int, str]) -> Any:
    """Canonical form of a class instance; see _canon."""
    if id(obj) in seen:
        return {"__ref__": seen[id(obj)]}
    seen[id(obj)] = path
    attrs = getattr(obj, "__dict__", None)
    if attrs is None:
        # Decimal, datetime and similar value types: their str is exact.
        return {"__value__": type(obj).__name__ + ":" + str(obj)}
    body = {k: _canon(attrs[k], path + "." + k, seen)
            for k in sorted(attrs) if k not in NONDETERMINISTIC_ATTRS}
    body["__class__"] = type(obj).__name__
    return body


def state_hash(instances: Dict[str, Any], public_only: bool = False) -> str:
    """
    SHA256 over the canonical state of all instances.

    Args:
        instances: class name to instance.
        public_only: True hashes only attributes not starting with "_",
            which is exactly the state the BFCL checker compares. False
            (default) also covers private state such as the current
            directory and random generators, which decides future calls.

    Returns:
        hex digest.
    """
    seen: Dict[int, str] = {}
    data = {}
    for name in sorted(instances):
        attrs = vars(instances[name])
        keys = [k for k in sorted(attrs)
                if not (public_only and k.startswith("_"))]
        data[name] = {k: _canon(attrs[k], name + "." + k, seen) for k in keys
                      if k not in NONDETERMINISTIC_ATTRS}
    blob = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class BFCLEnv:
    """
    The tool world of one task, with per call snapshots.

    Call ordinals are 1 based: call 1 is the first executed call. The
    snapshot list holds, at index i, the full state before call i + 1.
    """

    def __init__(self, entry: dict, long_context: Optional[bool] = None) -> None:
        """
        Args:
            entry: one BFCL question entry (id, initial_config,
                involved_classes, ...).
            long_context: defaults to True when the entry id says so,
                matching upstream's category test.
        """
        self.entry = entry
        if long_context is None:
            long_context = "long_context" in entry["id"]
        self.long_context = long_context
        self.instances: Dict[str, Any] = {}
        self.snapshots: List[Dict[str, Any]] = []
        self.call_log: List[str] = []
        self.restore_initial()

    # ── state lifecycle ───────────────────────────────────────────────
    def restore_initial(self) -> None:
        """Rebuild every instance from initial_config; clear history (FR)."""
        config = self.entry.get("initial_config", {})
        self.instances = {
            c: _new_instance(c, config, self.long_context)
            for c in self.entry["involved_classes"]
        }
        self.snapshots = []
        self.call_log = []

    def restore_before(self, k: int) -> None:
        """
        Restore the state taken immediately before call k (SR).

        Calls k and later are forgotten, so the next executed call is
        ordinal k again.

        Raises:
            IndexError: when no call k was executed.
        """
        if k < 1 or k > len(self.snapshots):
            raise IndexError("no snapshot before call %d (calls made: %d)"
                             % (k, len(self.snapshots)))
        self.instances = copy.deepcopy(self.snapshots[k - 1])
        del self.snapshots[k - 1:]
        del self.call_log[k - 1:]

    def state_hash(self, public_only: bool = False) -> str:
        """Hash of the current state; see module function state_hash."""
        return state_hash(self.instances, public_only=public_only)

    @property
    def calls_made(self) -> int:
        """Number of calls executed since the last restore."""
        return len(self.call_log)

    # ── dispatch ──────────────────────────────────────────────────────
    def method_map(self) -> Dict[str, Any]:
        """
        Public method name to bound method, in involved_classes order.

        Later classes win on a name collision, as in upstream.
        """
        mapping = {}
        for name in self.entry["involved_classes"]:
            members = inspect.getmembers(self.instances[name], inspect.ismethod)
            mapping.update({m: f for m, f in members if not m.startswith("_")})
        return mapping

    def call(self, name: str, args: list, kwargs: dict) -> str:
        """
        Execute one call: snapshot, dispatch, format the result.

        Never raises. Errors are returned in upstream form
        ("Error during execution: ...") and still count as a call, because
        upstream also records them as results.
        """
        self.snapshots.append(copy.deepcopy(self.instances))
        self.call_log.append(format_call(name, args, kwargs))
        method = self.method_map().get(name)
        try:
            if name in FORBIDDEN_NAMES:
                raise Exception("Function call %s is not allowed." % name)
            if method is None:
                raise NameError("name '%s' is not defined" % name)
            return _format_result(method(*args, **kwargs))
        except Exception as exc:  # backend errors become results, as upstream
            return ERROR_PREFIX + str(exc)

    def call_string(self, call: str) -> str:
        """Parse and execute one call string; parse errors become results."""
        try:
            name, args, kwargs = parse_call(call)
        except CallParseError as exc:
            return ERROR_PREFIX + str(exc)
        return self.call(name, args, kwargs)
