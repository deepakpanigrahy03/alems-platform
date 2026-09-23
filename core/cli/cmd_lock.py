# core/cli/cmd_lock.py
# Implements: alems lock show
# Scope: install-scoped when outside a project; project-scoped when inside one (8a rule 3).
# In 39.1 project resolution falls back to path_loader (8a rule 5).
from __future__ import annotations

import sys
from typing import List

from core.cli.main import register


@register("lock")
def handle_lock(argv: List[str]) -> int:
    """
    alems lock show

    Print the version lock for the current install and store to stdout.
    Writes nothing to disk.
    """
    if not argv or argv[0] == "show":
        _cmd_show()
        return 0
    print(f"alems lock: unknown subcommand '{argv[0]}'", file=sys.stderr)
    print("Usage: alems lock show", file=sys.stderr)
    return 1


def _cmd_show() -> None:
    """Resolve, build, and print the lock."""
    # Project-scoped: print resolved store path on line 1 (8a rule 4).
    # In 39.1 project resolution uses path_loader.
    try:
        import sys as _sys
        from pathlib import Path
        _sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts" / "tools"))
        from path_loader import get_alems_db_path
        db_path = get_alems_db_path()
        print(f"# store: {db_path}")
    except Exception as exc:
        print(f"# store: (unresolved: {exc})")

    from core.versioning import print_lock
    print_lock()
