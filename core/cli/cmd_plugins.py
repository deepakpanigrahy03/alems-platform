#!/usr/bin/env python3
"""
core/cli/cmd_plugins.py

Plugins subcommand handler for the alems CLI.
Registered as 'plugins' in core/cli/main.py.

Commands:
    alems plugins list                      list all registered plugins per family
    alems plugins list --family FAMILY      filter by family
    alems plugins list --all                include empty groups

Closes B6-6.
"""
from __future__ import annotations

import sys
from typing import List


def handle_plugins(argv: List[str]) -> int:
    """Entry point registered in core/cli/main.py as 'plugins'."""
    if not argv or argv[0] in ("-h", "--help"):
        _print_help()
        return 0

    cmd = argv[0]
    if cmd == "list":
        return _cmd_list(argv[1:])

    print(f"alems plugins: unknown subcommand '{cmd}'", file=sys.stderr)
    _print_help()
    return 1


def _cmd_list(argv: List[str]) -> int:
    """alems plugins list [--family FAMILY] [--all]"""
    import argparse
    p = argparse.ArgumentParser(prog="alems plugins list")
    p.add_argument(
        "--family",
        choices=["measurement", "execution", "semantic", "persistence", "output"],
        default=None,
        help="Filter output to one family",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="Show empty groups too",
    )
    args = p.parse_args(argv)

    # Bootstrap all registries before listing.
    # Each bootstrap is idempotent so safe to call here.
    _bootstrap_all()

    from core.registry.service import RegistryService
    report = RegistryService.report()

    found_any = False
    for family in sorted(report.keys()):
        if args.family and family != args.family:
            continue
        groups = report[family]
        non_empty = {g: e for g, e in groups.items() if e}
        if not non_empty and not args.all:
            continue
        print(f"\n[{family}]")
        for group, entries in sorted(groups.items()):
            if not entries and not args.all:
                continue
            print(f"  {group}  ({len(entries)})")
            for entry in entries:
                conform = entry.get("conformance", "UNKNOWN")
                warns   = entry.get("warnings", 0)
                warn_str = f"  {warns}W" if warns else ""
                print(f"    {entry['name']:30s}  {entry['class']:40s}  [{conform}{warn_str}]")
            found_any = True

    if not found_any:
        print("No plugins registered. Run 'pip install -e .' then retry.")
    return 0


def _bootstrap_all() -> None:
    """Bootstrap all registries. Failures are warnings, never fatal."""
    _try("reader bootstrap",    "core.readers.bootstrap",              "register_all_readers")
    _try("platform bootstrap",  "core.platform.bootstrap",             "register_all_platform_adapters")
    _try("adapter bootstrap",   "core.execution.adapters.bootstrap",   "register_all_adapters")
    _try("scorer bootstrap",    "core.execution.scorers.bootstrap",    "register_all_scorers")
    _try("framework bootstrap", "core.execution.frameworks.bootstrap", "register_all_frameworks")
    _try("output bootstrap",    "core.execution.outputs.bootstrap",    "register_all_outputs")


def _try(label: str, module: str, fn: str) -> None:
    try:
        import importlib
        m = importlib.import_module(module)
        getattr(m, fn)()
    except Exception as exc:
        print(f"[warning] {label}: {exc}")


def _print_help() -> None:
    print(
        "alems plugins <subcommand>\n"
        "\n"
        "Subcommands:\n"
        "  list              List all registered plugins per family\n"
        "\n"
        "Options for list:\n"
        "  --family FAMILY   Filter by family (measurement, execution, semantic,\n"
        "                    persistence, output)\n"
        "  --all             Show empty groups too\n"
    )
