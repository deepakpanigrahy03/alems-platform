"""
core/cli/cmd_sequence.py: `alems sequence <plan.yaml> [--dry-run] [--seed N]`
and `alems sequence --resume SESSION_ID`.

Dry run resolves and validates the plan and prints the effective settings,
the realised order and the required step kinds, without starting any load
or writing the store.
"""

import argparse
import json
import logging
import sys
from typing import List

logger = logging.getLogger(__name__)


def _args(argv):
    # type: (List[str]) -> argparse.Namespace
    p = argparse.ArgumentParser(prog="alems sequence")
    p.add_argument("plan", nargs="?", help="step plan YAML")
    p.add_argument("--dry-run", action="store_true", help="validate and print, run nothing")
    p.add_argument("--seed", type=int, default=None, help="override the plan seed")
    p.add_argument("--resume", type=int, default=None, metavar="SESSION_ID")
    p.add_argument("--kernel-pass", type=int, default=None, metavar="SESSION_ID",
                   help="after a session: one Nsight Compute pass per kernel_rw condition")
    return p.parse_args(argv)


def _dry_run(plan):
    """Print effective settings and the materialised order."""
    from core.sequencer import plan as plan_mod
    steps = plan_mod.materialize(plan)
    print(json.dumps({k: plan[k] for k in ("name", "seed", "cycles", "settle_s", "baseline",
                                           "on_baseline_failure")}, indent=1, default=str))
    print("kinds required:", sorted({s["kind"] for s in steps}))
    for s in steps:
        print("%4d  cycle %d  %-10s %-9s %s" % (s["planned_index"], s["cycle_id"], s["role"], s["kind"],
                                               s["condition"]))
    return 0


def _open_store():
    """DatabaseManager of the resolved store (same path as `alems run`), its path and config loader."""
    from core.config_loader import ConfigLoader
    from core.database.manager import DatabaseManager
    from core.storage.resolver import resolve_store
    loader = ConfigLoader()   # same loader run_experiment.py builds
    db = DatabaseManager(loader.get_db_config())
    return db, resolve_store(), loader   # lock sits next to the resolved store


def _kernel_pass(session_id):
    """Characterise each kernel_rw condition of a finished session (plugin alems-plugin-pravaha)."""
    try:
        from alems_plugin_pravaha.kernel_characterize import characterize_session
    except ImportError:
        print("kernel characterisation needs alems-plugin-pravaha", file=sys.stderr)
        return 2
    db, _, _ = _open_store()
    rows = characterize_session(db.db.conn, session_id)
    for r in rows:
        print(json.dumps({k: r.get(k) for k in ("pass_id", "matched_run_id", "status", "reason", "kernel_count",
                                                 "read_bytes", "write_bytes", "agreement_status")}))
    if not rows:
        print("no completed kernel_rw treatment runs in session %s" % session_id, file=sys.stderr)
    return 0 if rows and all(r["status"] == "completed" for r in rows) else 1


def handle_sequence(argv):
    # type: (List[str]) -> int
    """CLI entry; returns a process exit code."""
    from core.sequencer import plan as plan_mod
    from core.sequencer.kinds import all_kinds, validate_plan
    from core.sequencer.runner import Sequencer, acquire_lock, ResourceBusy
    a = _args(argv)
    if a.kernel_pass is not None:
        return _kernel_pass(a.kernel_pass)
    if not a.plan and a.resume is None:
        print("usage: alems sequence <plan.yaml> [--dry-run] [--seed N] | --resume SESSION_ID", file=sys.stderr)
        return 2
    try:
        if a.resume is None:
            plan = plan_mod.load(a.plan, tuple(all_kinds()), a.seed)
            errors = validate_plan(plan)
            if errors:
                raise plan_mod.PlanError("; ".join(errors))
            if a.dry_run:
                return _dry_run(plan)
        db, store_path, loader = _open_store()
        lock = acquire_lock(store_path)
        seq = Sequencer(db, loader)
        if a.resume is not None:
            row = db.db.conn.execute("SELECT resolved_plan_json FROM energy_validation_seq_sessions "
                                     "WHERE session_id = ?", (a.resume,)).fetchone()
            if not row:
                print("no session %s" % a.resume, file=sys.stderr)
                return 2
            return seq.resume(a.resume, json.loads(row[0]))
        return seq.start(plan)
    except plan_mod.PlanError as exc:
        print("plan error: %s" % exc, file=sys.stderr)
        return 2
    except ResourceBusy as exc:
        print("busy: %s" % exc, file=sys.stderr)
        return 3
