# core/cli/main.py
# Console entry point for the alems command (SPEC_39_1 section 8a).
# scripts/alems (bash) activates the venv and delegates here for Python subcommands.
# Existing bash subcommands (dev pull, dev sync, dev status, dev install,
# run, measure, report) are NOT handled here; they stay in scripts/alems.
from __future__ import annotations

import sys
from typing import List, Optional


# Registry of Python subcommand handlers.
# Key: first-level subcommand string (e.g. "lock", "dev").
# Value: callable(argv: List[str]) -> int
_COMMANDS: dict = {}


def register(name: str):
    """Decorator to register a subcommand handler under `name`."""
    def _decorator(fn):
        _COMMANDS[name] = fn
        return fn
    return _decorator


def main(argv: Optional[List[str]] = None) -> int:
    """
    Entry point called by the `alems` console script.

    Dispatch to registered Python subcommand or print help.
    """
    if argv is None:
        argv = sys.argv[1:]

    # Lazy-import subcommands so they register themselves.
    _load_builtin_commands()

    if not argv or argv[0] in ("-h", "--help"):
        _print_help()
        return 0

    cmd = argv[0]
    handler = _COMMANDS.get(cmd)
    if handler is None:
        # Unknown command: let the bash wrapper handle it (run, measure, etc.)
        # This path is reached only when invoked directly via Python entry point.
        print(f"alems: unknown command '{cmd}'", file=sys.stderr)
        print("Run 'alems --help' for usage.", file=sys.stderr)
        return 1

    return handler(argv[1:])


def _load_builtin_commands() -> None:
    """Register built-in command handlers directly."""
    from core.cli.cmd_lock import handle_lock
    from core.cli.cmd_dev import handle_dev
    _COMMANDS["lock"] = handle_lock
    _COMMANDS["dev"] = handle_dev


def _print_help() -> None:
    print(
        "alems <command> [args]\n"
        "\n"
        "Python commands (this entry point):\n"
        "  lock show       Print the version lock for the current install and store\n"
        "  dev golden      Golden verification system (capture, check, rebaseline)\n"
        "\n"
        "Bash commands (scripts/alems):\n"
        "  dev pull        git pull + auto-sync schema and configs\n"
        "  dev sync        Sync schema/configs without pulling\n"
        "  dev status      Show platform, DB, branch, schema version\n"
        "  dev install     Run full install\n"
        "  run <task-id>   Run an experiment\n"
        "  measure         Run energy measurement baseline\n"
        "  report          Generate experiment report\n"
        "\n"
        "Scope: project-scoped commands print the resolved project on the first output line.\n"
        "Project resolution is implemented in phase 39.2; until then path_loader is used.\n"
    )


if __name__ == "__main__":
    sys.exit(main())
