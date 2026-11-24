"""
core/cli/cmd_profile.py: `alems profile kernels [--match-run RUN_ID] [--match REGEX]
[--launch-count N] [--phase NAME] -- COMMAND ...`

Runs COMMAND once under Nsight Compute through the step kind that provides
`kernel_traffic` (plugin alems-plugin-pravaha) and stores one pass. Core only
dispatches; the profiler and its settings live in the plugin. The command's
interpreter is the one written in COMMAND, never the A-LEMS venv.
"""

import argparse
import json
import sys
from typing import List


def _args(argv):
    # type: (List[str]) -> argparse.Namespace
    p = argparse.ArgumentParser(prog="alems profile kernels")
    p.add_argument("--match-run", type=int, default=None, metavar="RUN_ID", help="reference run for agreement")
    p.add_argument("--match", default=None, metavar="REGEX", help="only kernels matching REGEX")
    p.add_argument("--launch-count", type=int, default=None, help="stop after N profiled launches")
    p.add_argument("--phase", default=None, help="workload phase label stored with the pass")
    p.add_argument("--probe", action="store_true", help="only report feasibility")
    p.add_argument("command", nargs=argparse.REMAINDER)
    return p.parse_args(argv)


def handle_profile(argv):
    # type: (List[str]) -> int
    if not argv or argv[0] != "kernels":
        print("usage: alems profile kernels [options] -- COMMAND ...", file=sys.stderr)
        return 2
    a = _args(argv[1:])
    from core.sequencer.kinds import plugin_kind_classes
    cls = plugin_kind_classes().get("kernel_traffic")
    if cls is None:
        print("kernel_traffic step kind not installed (pip install -e alems-plugin-pravaha)", file=sys.stderr)
        return 2
    mod = sys.modules[cls.__module__]
    if a.probe:
        print(json.dumps(mod.probe(), indent=1))
        return 0
    cmd = a.command[1:] if a.command[:1] == ["--"] else a.command
    if not cmd:
        print("missing COMMAND after --", file=sys.stderr)
        return 2
    from core.cli.cmd_sequence import _open_store
    db, _, _ = _open_store()
    row = mod.profile(db.db.conn, cmd, {"match": a.match, "launch_count": a.launch_count},
                      matched_run_id=a.match_run, interpreter=cmd[0], phase=a.phase)
    keys = ("pass_id", "status", "reason", "reason_detail", "feasibility", "kernel_count", "read_bytes",
            "write_bytes", "read_ratio", "write_ratio", "agreement_status")
    print(json.dumps({k: row.get(k) for k in keys}, indent=1))
    return 0 if row["status"] == "completed" else 1
