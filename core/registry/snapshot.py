#!/usr/bin/env python3
"""
================================================================================
REGISTRY SNAPSHOT  —  core/registry/snapshot.py
================================================================================

Purpose:
    Capture the registered set per family before and after 39.3 changes
    so the spec section 4 requirement (snapshots identical) is verifiable.

    Usage:
        python3 -m core.registry.snapshot --out /tmp/snapshot_before.json
        # apply changes
        python3 -m core.registry.snapshot --out /tmp/snapshot_after.json
        python3 -m core.registry.snapshot --diff /tmp/snapshot_before.json /tmp/snapshot_after.json

Author: Deepak Panigrahy
Spec:   SPEC_39_3, section 4
================================================================================
"""

import json
import sys
import socket
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def capture():
    # type: () -> dict
    """
    Capture current registration state across all groups.
    Returns a JSON-serializable dict: group -> sorted list of registered names.
    Also records the ReaderFactory selection result for energy, cpu, thermal
    (the critical measurement readers) via a dry-run capability probe.
    """
    from core.registry.service import RegistryService

    snapshot = {
        "host": socket.gethostname(),
        "groups": {},
        "factory_selection": {},
    }

    from core.registry.service import _GROUP_TABLE
    for group in sorted(_GROUP_TABLE.keys()):
        names = RegistryService.discover(group)
        snapshot["groups"][group] = sorted(names)

    # Dry-run factory selection for measurement-critical readers.
    # Uses real platform capabilities so the snapshot reflects actual
    # selection on this machine.
    try:
        from core.readers.factory import ReaderFactory
        from core.utils.platform import get_platform_capabilities
        caps = get_platform_capabilities()
        config = {}

        for family in ("energy", "cpu", "thermal", "disk"):
            getter = getattr(ReaderFactory, f"get_{family}_reader", None)
            if getter is None:
                continue
            try:
                reader = getter(config, caps)
                snapshot["factory_selection"][family] = type(reader).__name__
            except Exception as exc:
                snapshot["factory_selection"][family] = f"ERROR: {exc}"
    except Exception as exc:
        logger.warning("snapshot: factory selection probe failed: %s", exc)
        snapshot["factory_selection"]["_error"] = str(exc)

    return snapshot


def diff(before, after):
    # type: (dict, dict) -> dict
    """
    Compare two snapshots. Returns dict of differences.
    Empty dict means identical (pass).
    """
    diffs = {}

    before_groups = before.get("groups", {})
    after_groups  = after.get("groups", {})

    all_groups = set(before_groups) | set(after_groups)
    for group in sorted(all_groups):
        b = set(before_groups.get(group, []))
        a = set(after_groups.get(group, []))
        added   = sorted(a - b)
        removed = sorted(b - a)
        if added or removed:
            diffs[group] = {"added": added, "removed": removed}

    before_sel = before.get("factory_selection", {})
    after_sel  = after.get("factory_selection", {})
    sel_diffs = {}
    for family in set(before_sel) | set(after_sel):
        bv = before_sel.get(family, "MISSING")
        av = after_sel.get(family, "MISSING")
        if bv != av:
            sel_diffs[family] = {"before": bv, "after": av}
    if sel_diffs:
        diffs["_factory_selection"] = sel_diffs

    return diffs


def main():
    # type: () -> None
    import argparse
    p = argparse.ArgumentParser(description="Registry snapshot tool")
    p.add_argument("--out", help="Write snapshot JSON to this file")
    p.add_argument("--diff", nargs=2, metavar=("BEFORE", "AFTER"),
                   help="Diff two snapshot files")
    args = p.parse_args()

    if args.diff:
        with open(args.diff[0]) as f:
            before = json.load(f)
        with open(args.diff[1]) as f:
            after = json.load(f)
        result = diff(before, after)
        if result:
            print("DIFF FOUND — snapshot mismatch:")
            print(json.dumps(result, indent=2))
            sys.exit(1)
        else:
            print("OK — snapshots identical")
            sys.exit(0)

    snap = capture()
    out = json.dumps(snap, indent=2)
    if args.out:
        with open(args.out, "w") as f:
            f.write(out)
        print(f"Snapshot written to {args.out}")
    else:
        print(out)


if __name__ == "__main__":
    main()
