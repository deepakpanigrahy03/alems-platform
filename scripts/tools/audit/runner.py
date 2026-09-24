# scripts/tools/audit/runner.py
from __future__ import annotations
import sys
from typing import List


def audit_main(action: str, argv: List[str]) -> int:
    """
    Dispatch audit sub-subcommands.

    Args:
        action: capture | check | rebaseline
        argv:   remaining arguments

    Returns:
        0 on success, non-zero on failure.
    """
    if action == "capture":
        from scripts.tools.audit.capture import cmd_capture
        return cmd_capture(argv)
    if action == "check":
        from scripts.tools.audit.check import cmd_check
        return cmd_check(argv)
    if action == "rebaseline":
        from scripts.tools.audit.rebaseline import cmd_rebaseline
        return cmd_rebaseline(argv)
    print(f"alems dev audit: unknown action '{action}'", file=sys.stderr)
    return 1
