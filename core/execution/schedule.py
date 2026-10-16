"""
Scheduled profile queue (RAF R2 under alems run). Pure functions, no I/O.

A profile with a schedule section runs a seeded queue of goal executions
instead of the plain repetition loop. The schedule section carries:

    seeds:          block_order, policy_order, model_seed
    b0_repetitions: B0 goals per task
    b0_scope:       all (every task) | checks (tasks in a turn checking family)
    early_ordinal:  injection ordinal of every early point of the clean family
    families:       name -> {classes {cls: phase}, options [policy[@arm]],
                     points {point: repetitions}, max_injections, attempts,
                     turn_check}
    tasks:          task id -> {stratum, families [..], targets {family:
                     {point: number | {llm, tool} | {tool_name}}}}
    tables:         arm -> {"task|cls|point": policy}; an option TABLE@arm
                     takes its policy per block from this table (RAF R6, E3
                     token based and oracle arms)

A block is one task x class x point x repetition holding the family options
in a seeded order. With one repetition the block id is task|cls|point, the
id the pilot schedule used, so a pilot or E1 configuration converted to a
profile yields the identical queue (same ids, same option order, same B0
positions). Repetitions above one append |r<n>.
"""

import random
from typing import Any, Dict, Iterable, List, Optional, Tuple

ARM_FIXED = "fixed"


def parse_option(opt: str) -> Tuple[str, str]:
    """'SR_warm@escalate' -> ('SR_warm', 'escalate'); no arm means fixed."""
    policy, _, arm = opt.partition("@")
    return policy, arm or ARM_FIXED


TABLE = "TABLE"


def _resolve_table(cfg: Dict[str, Any], task: str, cls: str, point: str,
                   goal: Dict[str, Any]) -> Dict[str, Any]:
    """TABLE@arm: the policy of this cell from schedule.tables[arm]; others unchanged."""
    if goal["policy"] != TABLE:
        return goal
    key = "%s|%s|%s" % (task, cls, point)
    pol = ((cfg.get("tables") or {}).get(goal["arm"]) or {}).get(key)
    if not pol:
        raise ValueError("table %r has no policy for %s" % (goal["arm"], key))
    return dict(goal, policy=pol, table=goal["arm"])


def points_of(cfg: Dict[str, Any], task: str, family: str) -> List[str]:
    """Points of a family that this task has a target for, sorted."""
    pts = sorted(cfg["families"][family]["points"])
    have = cfg["tasks"][task].get("targets", {}).get(family) or {}
    if family == "clean":
        return [p for p in pts if p == "early" or p in have]
    return [p for p in pts if p in have]


def target(cfg: Dict[str, Any], task: str, family: str, phase: str,
           point: str) -> Tuple[Any, Optional[str]]:
    """Injection target (ordinal, tool_name) of one cell."""
    if family == "clean" and point == "early":
        return int(cfg["early_ordinal"]), None
    entry = cfg["tasks"][task]["targets"][family][point]
    if isinstance(entry, dict) and "tool_name" in entry:
        return "any", entry["tool_name"]
    k = entry[phase] if isinstance(entry, dict) else entry
    return int(k), None


def option_order(cfg: Dict[str, Any], block_id: str,
                 options: Iterable[str]) -> List[str]:
    """Seeded order of the options inside one block (pilot rule)."""
    # String seeds are hashed deterministically by random.Random (sha512).
    rng = random.Random("%s:%s" % (cfg["seeds"]["policy_order"], block_id))
    order = list(options)
    rng.shuffle(order)
    return order


def _selected(cfg: Dict[str, Any], task: str,
              families: Optional[Iterable[str]]) -> List[str]:
    keep = set(families) if families else None
    return sorted(f for f in cfg["tasks"][task]["families"]
                  if keep is None or f in keep)


def _goal(task, fam, cls, phase, point, rep, k, tool, opt, spec) -> Dict[str, Any]:
    policy, arm = parse_option(opt)
    return {"task": task, "family": fam, "cls": cls, "phase": phase,
            "point": point, "rep": rep, "ordinal": k, "tool_name": tool,
            "policy": policy, "arm": arm,
            "max_injections": int(spec.get("max_injections", 1)),
            "attempts": int(spec.get("attempts", 2)),
            "turn_check": bool(spec.get("turn_check", False))}


def _block_id(task: str, cls: str, point: str, rep: int, reps: int) -> str:
    base = "%s|%s|%s" % (task, cls, point)
    return base if reps == 1 else "%s|r%d" % (base, rep)


def build_blocks(cfg: Dict[str, Any],
                 families: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
    """Every (task, class, point, repetition) block, unshuffled."""
    blocks = []
    for task in sorted(cfg["tasks"]):
        for fam in _selected(cfg, task, families):
            spec = cfg["families"][fam]
            for cls in sorted(spec["classes"]):
                phase = spec["classes"][cls]
                for point in points_of(cfg, task, fam):
                    k, tool = target(cfg, task, fam, phase, point)
                    reps = int(spec["points"][point])
                    for rep in range(1, reps + 1):
                        bid = _block_id(task, cls, point, rep, reps)
                        goals = [_resolve_table(cfg, task, cls, point,
                                                _goal(task, fam, cls, phase, point,
                                                      rep, k, tool, opt, spec))
                                 for opt in option_order(cfg, bid, spec["options"])]
                        blocks.append({"id": bid, "kind": "block", "task": task,
                                       "family": fam, "goals": goals})
    return blocks


def build_b0(cfg: Dict[str, Any],
             families: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
    """
    B0 items: never injected, same retry budget as the pilot (two attempts).

    b0_scope all: every task with a selected family (pilot, E1).
    b0_scope checks: only tasks in a selected turn checking family (E2).
    """
    scope = cfg.get("b0_scope", "checks")
    items = []
    for task in sorted(cfg["tasks"]):
        fams = _selected(cfg, task, families)
        if not fams:
            continue
        check = any(cfg["families"][f].get("turn_check") for f in fams)
        if scope == "checks" and not check:
            continue
        for rep in range(1, int(cfg["b0_repetitions"]) + 1):
            goal = {"task": task, "family": None, "cls": "none", "phase": None,
                    "point": None, "rep": rep, "ordinal": None,
                    "tool_name": None, "policy": "B0", "arm": ARM_FIXED,
                    "max_injections": 0, "attempts": 2, "turn_check": check}
            items.append({"id": "%s|B0|%d" % (task, rep), "kind": "b0",
                          "task": task, "family": None, "goals": [goal]})
    return items


def build_queue(cfg: Dict[str, Any],
                families: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
    """Blocks in seeded order with B0 items inserted at seeded positions."""
    rng = random.Random(int(cfg["seeds"]["block_order"]))
    queue = build_blocks(cfg, families)
    rng.shuffle(queue)
    for item in build_b0(cfg, families):
        # Positions drawn sequentially from the same generator (pilot rule).
        queue.insert(rng.randint(0, len(queue)), item)
    for pos, item in enumerate(queue):
        item["position"] = pos
    return queue


def count_goals(queue: List[Dict[str, Any]]) -> Dict[str, int]:
    """Goal executions per kind and per family."""
    out = {"block": 0, "b0": 0}
    for item in queue:
        out[item["kind"]] += len(item["goals"])
        if item["kind"] == "block":
            key = "family:" + item["family"]
            out[key] = out.get(key, 0) + len(item["goals"])
    return out
