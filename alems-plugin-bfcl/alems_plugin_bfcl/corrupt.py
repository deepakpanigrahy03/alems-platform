"""
Deterministic corruption for the RAF contaminating failure classes.

Spec SPEC_RAF_R1 section 4.1, plan PLAN_RAF_R1_S8 D8, plausibility rule
added in session S8 after smoke review.

    silent_tool_output   the tool runs for real; one leaf of its returned
                         JSON result is replaced by a plausible wrong value
    wrong_model_step     one leaf of the model's tool call arguments is
                         replaced before the tool executes

Everything here is a pure function of (value, seed, pool): the same goal,
class and ordinal over the same trajectory always yields the same
corruption, so every recovery option of a cell sees the identical fault
(fair comparison, C2) and a reviewer can reproduce it.

Plausibility: a wrong string is drawn first from strings the agent has
already seen in this attempt (real entity names from earlier results and
arguments), so the fault reads like a real slip (another existing
directory, another real id). Reversal and suffixing are last fallbacks.
Each corruption records the rule used, so the paper can report the share
of each rule.
"""

import copy
import hashlib
import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

# A path is a tuple of dict keys and list indices from the root.
Path = Tuple[Any, ...]

# Rule names recorded in the corruption detail.
R_INT = "int_step"
R_FLOAT = "float_scale"
R_SWAP_LOCAL = "swap_local"      # another string of the same object
R_SWAP_SEEN = "swap_seen"        # a string seen earlier in the attempt
R_REVERSE = "reverse"
R_SUFFIX = "suffix"


def seed_of(goal_key: Any, cls: str, ordinal: int) -> int:
    """First 8 bytes of sha256(goal_key|class|ordinal) as an unsigned int."""
    blob = ("%s|%s|%s" % (goal_key, cls, ordinal)).encode("utf-8")
    return int.from_bytes(hashlib.sha256(blob).digest()[:8], "big")


def path_text(path: Path) -> str:
    """Readable path for the injection record: a/b[2]/c."""
    out = ""
    for p in path:
        out += "[%d]" % p if isinstance(p, int) else "/" + str(p)
    return out or "/"


def leaves(value: Any, path: Path = ()) -> List[Tuple[Path, Any]]:
    """
    Candidate leaves (int, float, str), sorted by path text.

    bool is excluded on purpose: it is a subclass of int, and flipping a
    flag is not the "plausible nearby value" the spec asks for.
    """
    out: List[Tuple[Path, Any]] = []
    if isinstance(value, dict):
        for k in value:
            out.extend(leaves(value[k], path + (k,)))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out.extend(leaves(v, path + (i,)))
    elif isinstance(value, bool) or value is None:
        pass
    elif isinstance(value, (int, float, str)):
        out.append((path, value))
    return sorted(out, key=lambda pv: path_text(pv[0]))


def strings_of(value: Any) -> List[str]:
    """Every string leaf of a JSON like value (for the seen pool)."""
    return [v for _, v in leaves(value) if isinstance(v, str)]


def _decimals(x: float) -> Optional[int]:
    """Decimal places as written; None for exponent notation (no rounding)."""
    s = repr(x)
    if "e" in s or "E" in s:
        return None
    return len(s.split(".", 1)[1]) if "." in s else 0


def replacement(old: Any, seed: int, local: Iterable[Any],
                seen: Iterable[str] = ()) -> Tuple[Any, str]:
    """
    Plausible wrong value of the same type, and the rule used.

    int    old + 1 + (seed mod 3)
    float  old x 1.5, rounded to the original decimals
    str    another string of the same object, else a string seen earlier in
           the attempt, else reversed, else old + "x"
    The result always differs from old.
    """
    if isinstance(old, int):
        return old + 1 + seed % 3, R_INT
    if isinstance(old, float):
        d = _decimals(old)
        new = old * 1.5 if d is None else round(old * 1.5, d)
        if new == old:  # 0.0 or rounding collapse: step the last digit
            new = old + (10.0 ** -(d or 1))
        return new, R_FLOAT
    # Different seed bits than the leaf choice keep both choices independent.
    pick = (seed >> 16)
    same = sorted({v for v in local if isinstance(v, str) and v != old})
    if same:
        return same[pick % len(same)], R_SWAP_LOCAL
    prior = sorted({v for v in seen if isinstance(v, str) and v != old and v})
    if prior:
        return prior[pick % len(prior)], R_SWAP_SEEN
    if len(old) > 1 and old[::-1] != old:
        return old[::-1], R_REVERSE
    return old + "x", R_SUFFIX


def _set(root: Any, path: Path, value: Any) -> Any:
    """Return root with the leaf at path replaced (root itself if path empty)."""
    if not path:
        return value
    node = root
    for p in path[:-1]:
        node = node[p]
    node[path[-1]] = value
    return root


def corrupt_value(value: Any, seed: int,
                  seen: Iterable[str] = ()) -> Tuple[Optional[Any], Dict[str, Any]]:
    """
    Corrupt one leaf of a JSON like value.

    Returns:
        (new_value, detail) or (None, detail with reason) when no leaf.
    """
    cands = leaves(value)
    if not cands:
        return None, {"applied": False, "reason": "no_candidate_leaf"}
    path, old = cands[seed % len(cands)]
    new, rule = replacement(old, seed, [v for _, v in cands], seen)
    out = _set(copy.deepcopy(value), path, new)
    return out, {"applied": True, "path": path_text(path),
                 "old": old, "new": new, "rule": rule}


def corrupt_args(args: Dict[str, Any], seed: int,
                 seen: Iterable[str] = ()) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """
    wrong_model_step: corrupt one argument of a parsed tool call.

    A call without usable arguments is recorded as not applicable rather
    than inventing an argument (keeps the fault a plausible model slip).
    """
    if not args or "__invalid_arguments__" in args:
        return None, {"applied": False, "reason": "no_arguments"}
    return corrupt_value(args, seed, seen)


def corrupt_result(text: str, seed: int, error_prefix: str,
                   seen: Iterable[str] = ()) -> Tuple[Optional[str], Dict[str, Any]]:
    """
    silent_tool_output: corrupt one leaf of a JSON tool result.

    Error results and non JSON text are not corrupted: a wrong value inside
    an error message, or a reversed sentence, would not be plausible.
    The output keeps json.dumps default formatting, which is the format the
    environment produced, so the change is invisible except for the value.
    """
    if text.startswith(error_prefix):
        return None, {"applied": False, "reason": "error_result"}
    try:
        value = json.loads(text)
    except ValueError:
        return None, {"applied": False, "reason": "non_json_result"}
    if not isinstance(value, (dict, list)):
        return None, {"applied": False, "reason": "scalar_result"}
    new, detail = corrupt_value(value, seed, seen)
    if new is None:
        return None, detail
    return json.dumps(new), detail
