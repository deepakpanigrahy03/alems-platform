#!/usr/bin/env python3
"""
RAF R6 split run check (coordinator item 11): cut a profile's queue into N
consecutive items files, in queue order, for N separate `alems run --items`
invocations.

    python scripts/raf/make_split_items.py --profile <profile.yaml> --parts 3 --out <prefix>

Writes <prefix>_1.tsv .. <prefix>_N.tsv (one block id per line). The queue is
built with the same seeded code the runner uses, so the split invocations run
the goals in exactly the order a single invocation would.
"""

import argparse
import os
import sys

import yaml

from core.execution.schedule import build_queue


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--parts", type=int, default=3)
    ap.add_argument("--out", required=True, help="output prefix")
    a = ap.parse_args()
    with open(a.profile) as fh:
        prof = yaml.safe_load(fh)
    ids = [it["id"] for it in build_queue(prof["schedule"]) if it["kind"] == "block"]
    if len(ids) < a.parts:
        sys.exit("queue has %d blocks, fewer than %d parts" % (len(ids), a.parts))
    size = -(-len(ids) // a.parts)                 # ceiling division
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    for i in range(a.parts):
        part = ids[i * size:(i + 1) * size]
        path = "%s_%d.tsv" % (a.out, i + 1)
        with open(path, "w") as fh:
            fh.write("# split %d of %d, %s\n" % (i + 1, a.parts, os.path.basename(a.profile)))
            fh.write("\n".join(part) + "\n")
        print("wrote %s (%d blocks)" % (path, len(part)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
