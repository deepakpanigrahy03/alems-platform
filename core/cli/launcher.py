#!/usr/bin/env python3
"""
core/cli/launcher.py

Console script entry point for the 'alems' command installed by pip.
Delegates immediately to scripts/alems (the canonical bash entry point)
so there is one code path regardless of how alems is invoked.

This file is pointed to by pyproject.toml [project.scripts].
It is regenerated into venv/bin/alems on every pip install -e .
"""
from __future__ import annotations

import os
import sys


def main() -> None:
    """Find scripts/alems and exec it, replacing this process."""
    # The engine root is two levels up from this file:
    # core/cli/launcher.py -> core/ -> <engine-root>/
    here = os.path.abspath(__file__)
    engine_root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    bash_entry = os.path.join(engine_root, "scripts", "alems")

    if not os.path.isfile(bash_entry):
        print(
            f"alems: cannot find scripts/alems at {bash_entry}",
            file=sys.stderr,
        )
        sys.exit(1)

    # exec replaces the current process with bash scripts/alems.
    # All argv (including subcommand and args) are passed through.
    os.execv("/bin/bash", ["/bin/bash", bash_entry] + sys.argv[1:])


if __name__ == "__main__":
    main()
