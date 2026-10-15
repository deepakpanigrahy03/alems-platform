"""
RAF R2 main study schedule (SPEC_RAF_R2 2.2 to 2.5, addenda A and B,
coordinator decisions P1 to P4 of 15 Oct). Pure functions, no I/O.

Generalizes pilot_schedule to class families: per family classes, options,
points with repetitions, targets per task, persistent arms. pilot_schedule.py
is left untouched so the pilot and E1 queues stay byte identical (B3).

Targets: cfg["tasks"][task]["targets"][family][point] is a number, a per
phase dict ({llm: k, tool: k}), or {tool_name: name} (ordinal any, A3, P1).
clean early always uses early_ordinal. A point missing from a task's targets
is skipped for that task (for example a task with no late write).

An option string is a policy with an optional arm: "SR_warm@escalate".
Block ids include the repetition, so option order is seeded per
(block, repetition) as spec 2.4 requires. B0 items are added only for tasks
in a selected family that checks turns (E2 base rate, B2, B6); E1 already
holds the clean family B0 runs.
"""

import random
from typing import Any, Dict, Iterable, List, Optional, Tuple

ARM_FIXED = "fixed"


def parse_option(opt: str) -> Tuple[str, str]:
    """'SR_warm@escalate' -> ('SR_warm', 'escalate'); no arm means fixed."""
    policy, _, arm = opt.partition("@")
    return policy, arm or ARM_FIXED


def points_of(cfg: Dict[str, Any], task: str, family: str) -> List[str]:
    """Points of a family that this task has a target for, sorted."""
    pts = sorted(cfg["families"][family]["points"])
    if family == "clean":
        return [p for p in pts if p == "early" or p in cfg["tasks"][task]["targets"]["clean"]]
    have = cfg["tasks"][task]["targets"].get(family) or {}
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
    """Seeded order of the options inside one block."""
    # String seeds are hashed deterministically by random.Random (sha512).
    rng = random.Random("%s:%s" % (cfg["seeds"]["policy_order"], block_id))
    order = list(options)
    rng.shuffle(order)
    return order


def _selected(cfg: Dict[str, Any], task: str,
              families: Optional[Iterable[str]]) -> List[str]:
    """Families of one task, restricted to the selected ones."""
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
                    for rep in range(1, int(spec["points"][point]) + 1):
                        bid = "%s|%s|%s|r%d" % (task, cls, point, rep)
                        goals = [_goal(task, fam, cls, phase, point, rep, k,
                                       tool, opt, spec)
                                 for opt in option_order(cfg, bid, spec["options"])]
                        blocks.append({"id": bid, "kind": "block", "task": task,
                                       "family": fam, "goals": goals})
    return blocks


def build_b0(cfg: Dict[str, Any],
             families: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
    """B0 items, checks on, for tasks in a selected turn checking family."""
    items = []
    for task in sorted(cfg["tasks"]):
        fams = _selected(cfg, task, families)
        if not any(cfg["families"][f].get("turn_check") for f in fams):
            continue
        for rep in range(1, int(cfg["b0_repetitions"]) + 1):
            goal = {"task": task, "family": None, "cls": "none", "phase": None,
                    "point": None, "rep": rep, "ordinal": None,
                    "tool_name": None, "policy": "B0", "arm": ARM_FIXED,
                    "max_injections": 0, "attempts": 1, "turn_check": True}
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
        # Positions drawn sequentially from the same generator.
        queue.insert(rng.randint(0, len(queue)), item)
    for pos, item in enumerate(queue):
        item["position"] = pos
    return queue


def count_goals(queue: List[Dict[str, Any]]) -> Dict[str, int]:
    """Goal executions per kind and per family, for projection and tests."""
    out = {"block": 0, "b0": 0}
    for item in queue:
        out[item["kind"]] += len(item["goals"])
        if item["kind"] == "block":
            key = "family:" + item["family"]
            out[key] = out.get(key, 0) + len(item["goals"])
    return out
