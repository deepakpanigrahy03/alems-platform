# core/cli/cmd_dev.py
# Implements: alems dev golden [capture|check|rebaseline]
# Golden system logic is implemented in WP 1b.
# This stub registers the command so the CLI framework is complete in WP 1a.
from __future__ import annotations

import sys
from typing import List

from core.cli.main import register


@register("dev")
def handle_dev(argv: List[str]) -> int:
    """
    alems dev <subcommand>

    golden capture       Capture baseline snapshots on this machine (WP 1b)
    golden check         Check current code against baseline (WP 1b)
    golden rebaseline    Create a new baseline version (WP 1b)
    """
    if not argv:
        print("alems dev: missing subcommand", file=sys.stderr)
        return 1

    sub = argv[0]
    if sub == "audit":
        return _cmd_audit(argv[1:])

    # Other dev subcommands (pull, sync, status, install) are handled by scripts/alems bash.
    print(f"alems dev: unknown subcommand '{sub}' (may be a bash command, use scripts/alems)", file=sys.stderr)
    return 1


def _cmd_audit(argv: List[str]) -> int:
    """Dispatch audit sub-subcommands."""
    action = argv[0] if argv else ""
    if action in ("capture", "check", "rebaseline"):
        try:
            from scripts.tools.audit.runner import audit_main
            return audit_main(action, argv[1:])
        except ImportError:
            print(
                f"alems dev audit {action}: audit system not yet built.",
                file=sys.stderr,
            )
            return 1
    print(f"alems dev audit: unknown action '{action}'", file=sys.stderr)
    print("Usage: alems dev audit [capture|check|rebaseline]", file=sys.stderr)
    return 1
