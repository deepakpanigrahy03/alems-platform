"""
Accepted wrong step for wrong_model_step (E2b; coordinator decision Oct 16).

E2 showed that numeric corruptions mostly produce invalid values: the tool
rejects them, the agent sees the error and retries, so the fault never
contaminates. A real wrong step must be accepted by the tool and leave the
world in a different state than the agent's intended call.

choose() tries candidates in a seeded order and keeps the first that passes
the acceptance test on a copy of the environment:
    1. the corrupted call returns no error, and
    2. its public state differs from the state the original call produces.
Candidates: first swaps to an existing value of the same type seen in the
attempt (another real id, name, amount), then the R1 rule based value.
No accepted candidate: the goal is planned as not applicable (reason
not_accepted), counted and reported, never weakened silently.

Runs at injection time, inside the attempt window (only then are the agent's
arguments known); its cost is recorded per goal as accept_ms.
"""

import copy
import random
import time
from typing import Any, Dict, List, Optional, Tuple

from . import corrupt as _corrupt
from .env import ERROR_PREFIX, state_hash

MAX_SWAPS_PER_LEAF = 4


def _trial(env: Any, name: str, args: Dict[str, Any]) -> Tuple[str, str]:
    """Run one call on a copy of the environment; (result text, public hash)."""
    t = copy.copy(env)
    t.instances = copy.deepcopy(env.instances)
    t.snapshots, t.call_log = [], []
    out = t.call(name, [], args)
    return out, state_hash(t.instances, public_only=True)


def _same_type(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return False
    if isinstance(a, int) and isinstance(b, int):
        return True
    if isinstance(a, float) and isinstance(b, (int, float)):
        return True
    return isinstance(a, str) and isinstance(b, str)


def candidates(args: Dict[str, Any], seed: int, seen: List[Any]) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Seeded list of (new_args, detail): existing value swaps first, then the R1 rule."""
    rng = random.Random(seed)
    leaves = [(p, v) for p, v in _corrupt.leaves(args)
              if not isinstance(v, bool) and isinstance(v, (int, float, str))]
    rng.shuffle(leaves)
    out = []
    for path, value in leaves:
        pool = sorted({s for s in seen if _same_type(value, s) and s != value}, key=repr)
        rng.shuffle(pool)
        for s in pool[:MAX_SWAPS_PER_LEAF]:
            new = _corrupt._set(copy.deepcopy(args), path, s)
            out.append((new, {"rule": "swap_existing", "path": _corrupt.path_text(path),
                              "old": value, "new": s}))
    rule_new, rule_detail = _corrupt.corrupt_args(args, seed, [s for s in seen if isinstance(s, str)])
    if rule_new is not None:
        out.append((rule_new, rule_detail))
    return out


def choose(env: Any, name: str, args: Dict[str, Any], seed: int,
           seen: List[Any]) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """First accepted candidate, or (None, detail with reason not_accepted)."""
    t0 = time.perf_counter_ns()
    orig_out, orig_hash = _trial(env, name, args)
    tried = 0
    for new, detail in candidates(args, seed, seen):
        tried += 1
        out, h = _trial(env, name, new)
        if not out.startswith(ERROR_PREFIX) and h != orig_hash:
            detail = dict(detail, applied=True, accepted=True, tried=tried,
                          original_error=orig_out.startswith(ERROR_PREFIX),
                          accept_ms=(time.perf_counter_ns() - t0) / 1e6)
            return new, detail
    return None, {"applied": False, "accepted": False, "reason": "not_accepted",
                  "tried": tried, "accept_ms": (time.perf_counter_ns() - t0) / 1e6}
