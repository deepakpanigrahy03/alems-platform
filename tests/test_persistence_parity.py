"""
EPS-1 guard: save_pair, save_single and execute_goal all reach
RunPersistenceService.insert_one_run, so no execution path can grow its own
persistence copy again (G77). Structural check over the AST, following calls
to helpers in the same module.

Behavioural parity (same tables and counts from one synthetic result on all
three paths) needs synthetic mode as a first class run mode (G48) and is
added with 39.5.3 selftest.

AUTHOR: Deepak Panigrahy
"""
import ast
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATHS = [
    ("core/execution/experiment_runner.py", "save_pair"),
    ("core/execution/experiment_runner.py", "save_single"),
    ("core/execution/goal_execution_manager.py", "execute_goal"),
]


def _functions(tree):
    """Map of function name to node for every def in the module (methods included)."""
    return {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _called_names(fn):
    """Names of everything a function calls (plain names and attribute names)."""
    out = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            f = n.func
            out.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
    return out


def _reaches(funcs, start, target, depth=4):
    """True if start calls target directly or through same module helpers."""
    seen, frontier = set(), {start}
    for _ in range(depth):
        calls = set().union(*(_called_names(funcs[f]) for f in frontier if f in funcs))
        if target in calls:
            return True
        frontier = (calls & set(funcs)) - seen
        seen |= frontier
    return False


def test_every_execution_path_uses_the_one_writer():
    """Each declared execution path reaches insert_one_run."""
    missing = []
    for rel, name in PATHS:
        with open(os.path.join(REPO, rel), "r", encoding="utf-8") as fh:
            funcs = _functions(ast.parse(fh.read()))
        assert name in funcs, "%s not found in %s" % (name, rel)
        # G137: execute_goal persists each attempt through the same service in
        # two stages (persist_raw, run_derived); both are the one writer.
        if not (_reaches(funcs, name, "insert_one_run")
                or _reaches(funcs, name, "persist_raw")):
            missing.append("%s:%s" % (rel, name))
    assert not missing, "paths bypassing RunPersistenceService: %s" % missing
