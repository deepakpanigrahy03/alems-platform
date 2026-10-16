"""
RAF R6 controller history: decisions and outcomes from the store (design v2).

Read side: count past controller decisions of one population against the
final outcomes of their goals. Write side: store this goal's controller
decisions as raf.controller annotations after execute_goal returns.

The posterior is never stored; it is recomputed from these immutable facts,
so any decision can be rebuilt from its recorded cut_id (design v2 section 6).
Read only queries plus one annotation write through db.insert_span_annotations
(the path core already uses for plugin annotations, INV-21).
"""

import json
import time
from typing import Any, Dict, List, Optional, Tuple

ANNOTATION_TYPE = "raf.controller"
ANNOTATION_SOURCE = "alems-plugin-bfcl"
POP_KEYS = ("params_version", "data_tag", "decider", "arm", "study_id")
ABANDON = "ABANDON"

# Decision rows joined to their goal through the anchoring attempt span.
_DECISIONS = (
    "SELECT a.annotation_id, ga.goal_id, a.payload "
    "FROM span_annotations a JOIN goal_attempt ga ON ga.span_id = a.span_id "
    "WHERE a.annotation_type = ? AND a.annotation_id <= ? "
    "ORDER BY a.annotation_id")
_MAX_ID = "SELECT MAX(annotation_id) FROM span_annotations WHERE annotation_type = ?"
# Final goal outcome: MAX over attempts; NULL everywhere = no evidence.
_OUTCOME = ("SELECT MAX(pass_fail) FROM goal_attempt "
            "WHERE goal_id = ? AND pass_fail IS NOT NULL")
_ANCHOR = ("SELECT span_id FROM goal_attempt "
           "WHERE goal_id = ? AND attempt_number = ?")


def count_key(cls: str, option: str) -> str:
    """JSON friendly key for (failure class, option)."""
    return "%s|%s" % (cls, option)


def _payload(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}


def _in_population(p: Dict[str, Any], pop: Dict[str, Any]) -> bool:
    return all(p.get(k) == pop.get(k) for k in POP_KEYS)


def _decisions_by_goal(conn: Any, pop: Dict[str, Any], cut_id: int,
                       exclude_goal_id: Optional[int]) -> Dict[int, Dict[int, Tuple[str, str]]]:
    """goal_id -> decision attempt -> (failure class, option); first row wins."""
    out: Dict[int, Dict[int, Tuple[str, str]]] = {}
    for _aid, goal_id, raw in conn.execute(_DECISIONS, (ANNOTATION_TYPE, cut_id)):
        if goal_id == exclude_goal_id:
            continue
        p = _payload(raw)
        if not _in_population(p, pop):
            continue
        att = p.get("decision_attempt")
        if att is None:
            continue
        # Ordered by annotation_id: a duplicate (goal, attempt) never replaces
        # the first row, so replays cannot inflate counts (design v2 section 4).
        out.setdefault(goal_id, {}).setdefault(
            int(att), (str(p.get("failure_class")), str(p.get("option"))))
    return out


def _credit(counts: Dict[str, List[int]], opts: List[Tuple[str, str]],
            passed: bool) -> None:
    """Item 8 (a): last option of a passed goal succeeds, every other one fails."""
    last = len(opts) - 1
    for i, (cls, opt) in enumerate(opts):
        if opt == ABANDON:
            continue
        c = counts.setdefault(count_key(cls, opt), [0, 0])
        if passed and i == last:
            c[0] += 1
        else:
            c[1] += 1


def read_history(conn: Any, pop: Dict[str, Any], cut_id: Optional[int] = None,
                 exclude_goal_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Counts for one population up to cut_id (default: everything visible now).

    Returns {"counts": {"cls|option": [successes, failures]}, "cut_id": int,
    "goals": goals with evidence, "query_ns": read time}.
    """
    t0 = time.perf_counter_ns()
    if cut_id is None:
        row = conn.execute(_MAX_ID, (ANNOTATION_TYPE,)).fetchone()
        cut_id = int(row[0]) if row and row[0] is not None else 0
    counts: Dict[str, List[int]] = {}
    goals = 0
    for goal_id, by_att in _decisions_by_goal(conn, pop, cut_id, exclude_goal_id).items():
        row = conn.execute(_OUTCOME, (goal_id,)).fetchone()
        if row is None or row[0] is None:
            continue                     # not scored: no evidence
        goals += 1
        _credit(counts, [by_att[a] for a in sorted(by_att)], bool(row[0]))
    return {"counts": counts, "cut_id": cut_id, "goals": goals,
            "query_ns": time.perf_counter_ns() - t0}


def write_decisions(db: Any, conn: Any, goal_id: int,
                    decisions: Dict[int, Dict[str, Any]]) -> Tuple[int, int]:
    """
    Store controller decisions of one finished goal; returns (written, skipped).

    Each decision before attempt n hangs on the span of attempt n-1, the
    attempt whose failure it reacts to. Records without a population (for
    example the R2 escalate_stub arm) are not controller decisions: skipped.
    """
    rows, skipped = [], 0
    for att in sorted(decisions):
        entry = decisions[att] or {}
        if att < 2 or not all(k in entry for k in POP_KEYS):
            continue
        anchor = conn.execute(_ANCHOR, (goal_id, att - 1)).fetchone()
        if anchor is None or not anchor[0]:
            skipped += 1                 # attempt n-1 had no span: undercount only
            continue
        payload = dict(entry, decision_attempt=att, goal_id=goal_id)
        rows.append({"span_id": anchor[0], "annotation_type": ANNOTATION_TYPE,
                     "annotation_version": "1", "source": ANNOTATION_SOURCE,
                     "payload": payload})
    if rows:
        with db.transaction():
            db.insert_span_annotations(rows)
    return len(rows), skipped
