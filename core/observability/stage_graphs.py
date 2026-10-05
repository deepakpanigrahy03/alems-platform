"""
Declared stage graphs for the three v1 execution paths (design 39.5.2 addendum).

Graphs live in code, never in config, so a graph version is tied to a git
commit. Each definition is canonicalized and hashed; the hash is the identity
stored on every stage_event row. Any node or edge change requires a new
graph_version (addendum rule 1).

Node fields beyond stage_id are stage contract declarations (design 14.5):
  empty_ok        zero rows written is a successful outcome (outcome=empty)
  unavailable_ok  stage may legitimately report outcome=unavailable
  partial_ok      swallowed failures yield outcome=partial, not failed (39.5.2d)
  scope           run | pair | goal (where the work really happens)
Declaring these in the hashed graph makes the stage contract versioned,
instead of being decided ad hoc at call sites.
"""
import hashlib
import json
from typing import Dict, List, Optional, Tuple

# Master 13.1 vocabulary; new ids require a master amendment.
STAGE_IDS = (
    "setup", "baseline", "measure", "persist_run", "persist_samples", "spans",
    "attribution", "residual", "quality", "hooks", "etl_phase", "etl_hardware",
    "integrity", "outputs",
)

STAGE_VERSION = "1.0.0"  # v1 compatibility instrumentation; 39.5.5 bumps


def _node(stage_id, empty_ok=False, unavailable_ok=False, scope="run", partial_ok=False):
    # type: (str, bool, bool, str, bool) -> Dict[str, object]
    """Build one node; validates the id against the master vocabulary."""
    if stage_id not in STAGE_IDS:
        raise ValueError("unknown stage_id %s" % stage_id)
    # partial_ok is part of the node, so it is covered by graph_hash (2c D3)
    return {"stage_id": stage_id, "empty_ok": empty_ok,
            "unavailable_ok": unavailable_ok, "scope": scope,
            "partial_ok": partial_ok}


def _req(src, dst, carries=()):
    # type: (str, str, Tuple[str, ...]) -> Dict[str, object]
    """requires edge: dst consumes src output; src failure propagates."""
    return {"from": src, "to": dst, "kind": "requires", "carries": list(carries)}


def _ord(src, dst):
    # type: (str, str) -> Dict[str, object]
    """orders edge: sequence only, no status propagation."""
    return {"from": src, "to": dst, "kind": "orders", "carries": []}


# Post run stages common to save_pair and save_single (design 15.3).
# quality, residual and spans may legitimately write zero rows (no scorer
# configured, no residual rows under current E20 table, no span writer
# output for linear runs), so they are empty_ok. persist_run never is.
def _post_run_nodes(pair):
    # type: (bool) -> List[Dict[str, object]]
    """Nodes shared by save_pair and save_single."""
    shared = "pair" if pair else "run"
    return [
        _node("persist_run"),
        _node("persist_samples", unavailable_ok=True, partial_ok=True),
        _node("spans", empty_ok=True),
        _node("attribution"),
        _node("residual", empty_ok=True),
        _node("quality", empty_ok=True, unavailable_ok=True),
        _node("etl_phase", empty_ok=True, scope=shared, partial_ok=True),
        _node("etl_hardware", empty_ok=True, scope=shared, partial_ok=True),
    ]


def _post_run_edges():
    # type: () -> List[Dict[str, object]]
    """
    Data dependencies of the shared persistence core (RunPersistenceService).
    Only requires edges verified against run_persistence.py; orders edges
    are added per path where the code order is known.
    """
    return [
        _req("persist_run", "persist_samples", ("runs",)),
        _req("persist_run", "spans", ("runs",)),
        _req("persist_samples", "etl_hardware", ("cpu_samples",)),
        _req("persist_samples", "etl_phase", ("energy_samples",)),
        _req("etl_phase", "attribution", ("orchestration_events",)),
        _req("persist_samples", "attribution", ("energy_samples",)),
        _req("attribution", "residual", ("energy_attribution",)),
        _req("persist_run", "quality", ("runs",)),
    ]


def _execute_goal():
    # type: () -> Dict[str, object]
    """
    execute_goal (1.2.0; 1.1.0 plus partial_ok): the persistence core plus quality, which is goal
    scoped (scored once on the final result, recorded on the winning run).
    setup, baseline, measure happen in the harness before persistence and
    belong to the 39.5.5 pipeline; they are not v1 persistence stages.
    """
    nodes = _post_run_nodes(False)
    for n in nodes:
        if n["stage_id"] == "quality":
            n["scope"] = "goal"
    edges = _post_run_edges() + [_ord("residual", "spans"), _ord("spans", "quality")]
    return {"graph_id": "execute_goal", "graph_version": "1.2.0",
            "nodes": nodes, "edges": edges}


GRAPHS = {
    "save_pair": {"graph_id": "save_pair", "graph_version": "1.2.0",
                  "nodes": _post_run_nodes(True),
                  "edges": _post_run_edges() + [_ord("spans", "persist_samples"),
                                                _ord("residual", "quality")]},
    "save_single": {"graph_id": "save_single", "graph_version": "1.2.0",
                    "nodes": _post_run_nodes(False),
                    "edges": _post_run_edges() + [_ord("spans", "persist_samples"),
                                                _ord("residual", "quality")]},
    "execute_goal": _execute_goal(),
}


def canonical_json(obj):
    # type: (object) -> str
    """
    Canonical JSON for hashing.

    Graph definitions contain only strings, booleans and lists, so sorted
    keys, no whitespace and UTF-8 match RFC 8785 for this input domain.
    Floats are rejected because their RFC 8785 form differs from json.dumps.
    """
    def _check(v):
        if isinstance(v, float):
            raise TypeError("floats not allowed in stage graphs")
        if isinstance(v, dict):
            for x in v.values():
                _check(x)
        if isinstance(v, list):
            for x in v:
                _check(x)
    _check(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def graph_hash(graph_id):
    # type: (str) -> str
    """SHA-256 of the canonical definition of a declared graph."""
    return hashlib.sha256(canonical_json(GRAPHS[graph_id]).encode("utf-8")).hexdigest()


def node(graph_id, stage_id):
    # type: (str, str) -> Optional[Dict[str, object]]
    """Return the node declaration or None when the stage is not applicable."""
    for n in GRAPHS[graph_id]["nodes"]:
        if n["stage_id"] == stage_id:
            return n
    return None


def requires_of(graph_id, stage_id):
    # type: (str, str) -> List[str]
    """Stage ids this stage requires (status propagation sources)."""
    return [e["from"] for e in GRAPHS[graph_id]["edges"]
            if e["to"] == stage_id and e["kind"] == "requires"]


def topological_order(graph_id):
    # type: (str) -> List[str]
    """Kahn order over all edges; raises ValueError on a cycle."""
    g = GRAPHS[graph_id]
    ids = [n["stage_id"] for n in g["nodes"]]
    indeg = {i: 0 for i in ids}
    for e in g["edges"]:
        indeg[e["to"]] += 1
    ready = [i for i in ids if indeg[i] == 0]
    out = []  # type: List[str]
    while ready:
        cur = ready.pop(0)
        out.append(cur)
        for e in g["edges"]:
            if e["from"] == cur:
                indeg[e["to"]] -= 1
                if indeg[e["to"]] == 0:
                    ready.append(e["to"])
    if len(out) != len(ids):
        raise ValueError("cycle in graph %s" % graph_id)
    return out
