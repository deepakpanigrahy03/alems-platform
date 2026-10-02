"""
Shim: persistence coverage proof, now core.validation.persistence.coverage.

    python scripts/tools/persistence_coverage.py <store.db> <old_ids> <new_ids>

Kept so existing evidence commands keep working; replaced by
alems validate persistence --against in 39.5.6.

AUTHOR: Deepak Panigrahy
"""
import os
import sys

# Engine root on sys.path so the script runs from any directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.validation.persistence import coverage  # noqa: E402


def main() -> int:
    """Print one line per declared table and return 1 on LOST or ERR."""
    store, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
    rows = coverage(store, [int(x) for x in old.split(",")], [int(x) for x in new.split(",")])
    bad = 0
    for r in rows:
        print("%-36s %-8s old=%s new=%s" % (r.table, r.status, r.old, r.new))
        bad += r.status != "OK" and r.status != "NEW"
    print("SUMMARY tables=%d not_ok=%d" % (len(rows), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
