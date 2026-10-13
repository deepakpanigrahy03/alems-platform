"""
RAF LH 01 pilot schedule (design v2 FINAL section 10). Pure functions, no I/O.

A block is one task x one (class, point); it holds the four recovery
policies in a seeded order. B0 items are single goal executions placed at
seeded queue positions. The same configuration always yields the same queue,
so a resumed or repeated session runs the identical order.
"""

import random
from typing import Any, Dict, List


def ordinal(cfg: Dict[str, Any], task: str, phase: str, point: str) -> int:
    """Injection ordinal for one task, call type and point (design 5.3)."""
    if point == "early":
        return int(cfg["early_ordinal"])
    return int(cfg["tasks"][task]["late"][phase])


def policy_order(cfg: Dict[str, Any], block_id: str) -> List[str]:
    """Seeded order of the four policies inside one block."""
    # String seeds are hashed deterministically by random.Random (sha512).
    rng = random.Random("%s:%s" % (cfg["seeds"]["policy_order"], block_id))
    order = list(cfg["policies"])
    rng.shuffle(order)
    return order


def build_blocks(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every (task, class, point) block with its goal items, unshuffled."""
    blocks = []
    for task in sorted(cfg["tasks"]):
        for cls in sorted(cfg["classes"]):
            phase = cfg["classes"][cls]
            for point in cfg["points"]:
                bid = "%s|%s|%s" % (task, cls, point)
                k = ordinal(cfg, task, phase, point)
                goals = [{"task": task, "cls": cls, "phase": phase,
                          "point": point, "ordinal": k, "policy": p}
                         for p in policy_order(cfg, bid)]
                blocks.append({"id": bid, "kind": "block", "task": task,
                               "goals": goals})
    return blocks


def build_b0(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """B0 single goal items: one per task and repetition, never injected."""
    items = []
    for task in sorted(cfg["tasks"]):
        for rep in range(1, int(cfg["b0_repetitions"]) + 1):
            goal = {"task": task, "cls": "none", "phase": None,
                    "point": None, "ordinal": None, "policy": "B0"}
            items.append({"id": "%s|B0|%d" % (task, rep), "kind": "b0",
                          "task": task, "goals": [goal]})
    return items


def build_queue(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Blocks in seeded order with B0 items inserted at seeded positions."""
    rng = random.Random(int(cfg["seeds"]["block_order"]))
    queue = build_blocks(cfg)
    rng.shuffle(queue)
    for item in build_b0(cfg):
        # Positions drawn sequentially from the same generator.
        queue.insert(rng.randint(0, len(queue)), item)
    for pos, item in enumerate(queue):
        item["position"] = pos
    return queue


def count_goals(queue: List[Dict[str, Any]]) -> Dict[str, int]:
    """Goal executions per kind, for the projection and the tests."""
    out = {"block": 0, "b0": 0}
    for item in queue:
        out[item["kind"]] += len(item["goals"])
    return out
