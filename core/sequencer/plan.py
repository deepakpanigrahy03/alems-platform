"""
core/sequencer/plan.py: load, validate and resolve a step plan.

A plan names conditions (blocks); the sequencer turns it into cycles of
brackets (pre idle, treatment, post idle). Resolution fixes everything the run
will use (defaults from ~/.alemsrc, seed, order) and is stored verbatim with
its hash, so a replay after a config change is detectable.

Defaults (never hardcoded in callers; environment layer, shell wins):
    ALEMS_SEQ_BASELINE_S  idle duration per baseline step (default 60)
    ALEMS_SEQ_SETTLE_S    minimum settle before each step (default 30)
    ALEMS_SEQ_CYCLES      cycles when the plan gives none (default 1)
Order: counterbalanced. Two conditions alternate AB and BA across cycles
(the seed picks which cycle starts with which). More conditions use the rows
of a seeded cyclic Latin square, one row per cycle, so each condition takes
each position equally often when cycles is a multiple of the condition count.
The realised order is stored with the session.
"""

import hashlib
import json
import os
import random
from typing import Any, Dict, List

PLAN_SCHEMA_VERSION = 1
KNOWN_KINDS_CORE = ("idle", "profile")
_DEFAULTS = {"ALEMS_SEQ_BASELINE_S": 60.0, "ALEMS_SEQ_SETTLE_S": 30.0, "ALEMS_SEQ_CYCLES": 1}


class PlanError(ValueError):
    """Plan is invalid; raised before any step runs."""


def _env_default(name):
    # type: (str) -> float
    """Numeric default from the environment layer, else the named default."""
    raw = os.environ.get(name)
    try:
        return float(raw) if raw else float(_DEFAULTS[name])
    except ValueError:
        raise PlanError("%s=%r is not a number" % (name, raw))


def load(path, known_kinds=KNOWN_KINDS_CORE, seed_override=None):
    # type: (str, tuple, int) -> Dict[str, Any]
    """Read, validate and resolve a plan file; returns the resolved plan dict."""
    import yaml
    with open(path, "rb") as fh:
        raw = fh.read()
    doc = yaml.safe_load(raw) or {}
    resolved = resolve(doc, known_kinds, seed_override)
    resolved["source_sha256"] = hashlib.sha256(raw).hexdigest()
    resolved["path"] = os.path.abspath(path)
    return resolved


def resolve(doc, known_kinds, seed_override=None):
    # type: (Dict[str, Any], tuple, int) -> Dict[str, Any]
    """Validate and fill defaults; raises PlanError on any problem."""
    seq = doc.get("sequence") or {}
    blocks = doc.get("blocks") or []
    if not blocks:
        raise PlanError("plan has no blocks")
    baseline = dict(seq.get("baseline") or {"kind": "idle"})
    baseline.setdefault("duration_s", _env_default("ALEMS_SEQ_BASELINE_S"))
    seen = set()
    for b in blocks:
        _check_block(b, known_kinds, seen)
    _check_kind(baseline, known_kinds, "baseline")
    seed = seed_override if seed_override is not None else seq.get("seed")
    return {
        "schema_version": PLAN_SCHEMA_VERSION, "name": seq.get("name"),
        "seed": seed, "cycles": int(seq.get("cycles") or _env_default("ALEMS_SEQ_CYCLES")),
        "settle_s": float(seq.get("settle_s", _env_default("ALEMS_SEQ_SETTLE_S"))),
        "baseline": baseline,
        "on_baseline_failure": seq.get("on_baseline_failure", "skip_treatment"),
        "gate": seq.get("gate") or {},
        "blocks": blocks,
        # Source hash: of the plan document; load() replaces it with the file bytes hash
        "source_sha256": hashlib.sha256(json.dumps(doc, sort_keys=True, default=str).encode()).hexdigest(),
    }


def _check_block(block, known_kinds, seen):
    # type: (Dict[str, Any], tuple, set) -> None
    """One block: unique condition, known treatment kind."""
    cond = block.get("condition")
    if not cond or cond in seen:
        raise PlanError("block condition missing or duplicate: %r" % cond)
    seen.add(cond)
    _check_kind(block.get("treatment") or {}, known_kinds, "block %s" % cond)


def _check_kind(step, known_kinds, where):
    # type: (Dict[str, Any], tuple, str) -> None
    """Unknown kinds are rejected at plan time, before any overnight run."""
    if step.get("kind") not in known_kinds:
        raise PlanError("%s: unknown kind %r (known: %s)" % (where, step.get("kind"), ", ".join(known_kinds)))


def cycle_orders(blocks, cycles, seed):
    # type: (List[Dict[str, Any]], int, Any) -> List[List[Dict[str, Any]]]
    """
    Block order per cycle: seeded base order, then cyclic Latin rows
    (row k = base rotated by k). With two blocks this is AB, BA, AB, ...
    """
    base = list(blocks)
    random.Random(seed).shuffle(base)
    n = len(base)
    return [base[c % n:] + base[:c % n] for c in range(cycles)]


def materialize(plan):
    # type: (Dict[str, Any]) -> List[Dict[str, Any]]
    """All planned steps in run order: per cycle, per block, pre, treatment, post."""
    steps, index = [], 0
    for cycle, order in enumerate(cycle_orders(plan["blocks"], plan["cycles"], plan["seed"])):
        for block in order:
            bracket = "c%d:%s" % (cycle, block["condition"])
            for role, spec in (("pre", plan["baseline"]), ("treatment", block["treatment"]),
                               ("post", plan["baseline"])):
                steps.append({"cycle_id": cycle, "bracket_id": bracket, "condition": block["condition"],
                              "role": role, "kind": spec["kind"], "params": spec, "planned_index": index})
                index += 1
    return steps


def realized_order(steps):
    # type: (List[Dict[str, Any]]) -> List[List[str]]
    """Condition order per cycle, as stored with the session."""
    out = {}  # type: Dict[int, List[str]]
    for s in steps:
        if s["role"] == "treatment":
            out.setdefault(s["cycle_id"], []).append(s["condition"])
    return [out[c] for c in sorted(out)]


def plan_sha256(plan):
    # type: (Dict[str, Any]) -> str
    """Hash of the resolved plan (what actually runs)."""
    return hashlib.sha256(json.dumps(plan, sort_keys=True, default=str).encode()).hexdigest()
