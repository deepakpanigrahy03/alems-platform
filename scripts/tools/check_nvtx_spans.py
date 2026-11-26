#!/usr/bin/env python3
"""
scripts/tools/check_nvtx_spans.py: do NVTX ranges in an Nsight Systems report
agree with the spans A-LEMS stored for the same run?

Why: phase markers are only useful for attributing GPU kernels to agent phases
if the ranges a profiler shows are the phases A-LEMS measured. This compares,
for every span of the run that has an NVTX range (matched by span id in the
range message), the duration and the start offset from the run's root span.
Absolute times are not compared: the profiler and A-LEMS have different clock
zero points; durations and offsets are clock independent.

Usage:
  nsys export --type sqlite --output report.sqlite report.nsys-rep
  python3 scripts/tools/check_nvtx_spans.py --nsys report.sqlite --store <store.db> --run-id N
Tolerance: --tol-ms, else ALEMS_NVTX_TOL_MS, else 1.0 ms. Read only on both files,
unless --write: then one row goes to energy_validation_nvtx_agreement (e007, method
nvtx_span_agreement_v1) with the report path, its sha256 and the nsys version.
Exit 0 when every matched span agrees and at least one matched; 1 otherwise.
"""

import argparse
import hashlib
import os
import re
import sqlite3
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

METHOD_ID = "nvtx_span_agreement_v1"

_ID = re.compile(r"\[([0-9a-fA-F-]{8,})\]\s*$")


def nvtx_ranges(path):
    # type: (str) -> Dict[str, Tuple[int, int]]
    """span_id -> (start, end) from the report's NVTX events (text inline or via StringIds)."""
    con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    has_strings = bool(con.execute("SELECT 1 FROM sqlite_master WHERE name='StringIds'").fetchone())
    sql = ("SELECT e.start, e.end, COALESCE(e.text, s.value) FROM NVTX_EVENTS e "
           "LEFT JOIN StringIds s ON s.id = e.textId WHERE e.end IS NOT NULL" if has_strings else
           "SELECT start, end, text FROM NVTX_EVENTS WHERE end IS NOT NULL")
    out = {}
    for start, end, text in con.execute(sql):
        m = _ID.search(text or "")
        if m:
            out[m.group(1)] = (start, end)
    return out


def stored_spans(path, run_id):
    # type: (str, int) -> List[Tuple[str, str, str, int, int, str]]
    """(span_id, kind, name, start_ns, end_ns, parent) of the run's closed spans."""
    con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    return con.execute("SELECT span_id, kind, name, start_ns, end_ns, parent_span_id FROM spans "
                       "WHERE run_id = ? AND end_ns IS NOT NULL ORDER BY start_ns", (run_id,)).fetchall()


def compare(spans, ranges, tol_ns, stats=None):
    # type: (list, dict, int, Optional[Dict[str, Any]]) -> Tuple[List[str], int, int]
    """Report lines, matched count, failures; fills stats (max offsets in ms) when given."""
    stats = {} if stats is None else stats
    stats.update(max_start_offset_ms=None, max_duration_diff_ms=None)
    matched = [s for s in spans if s[0] in ranges]
    if not matched:
        return ["no span of this run has an NVTX range (plugins.nvtx.enabled or ALEMS_NVTX_ENABLED, "
                "and the nvtx package)"], 0, 1
    root = next((s for s in matched if s[5] is None), matched[0])
    r0, n0 = root[3], ranges[root[0]][0]
    lines, bad = [], 0
    for sid, kind, name, s0, s1, _ in matched:
        n_start, n_end = ranges[sid]
        d_dur = abs((n_end - n_start) - (s1 - s0))
        d_off = abs((n_start - n0) - (s0 - r0))
        if kind == "llm_call":
            # NVTX range = the Python call around the request; stored span = request to
            # last token (span_builder 45.8). Rule: the range contains the span.
            a0, a1 = n_start - n0 + r0, n_end - n0 + r0      # range on the store clock
            early, late = max(0, a0 - s0), max(0, s1 - a1)   # how far the span sticks out
            d_off, d_dur = early, late
        stats["max_duration_diff_ms"] = max(stats["max_duration_diff_ms"] or 0.0, d_dur / 1e6)
        stats["max_start_offset_ms"] = max(stats["max_start_offset_ms"] or 0.0, d_off / 1e6)
        ok = d_dur <= tol_ns and d_off <= tol_ns
        bad += 0 if ok else 1
        lines.append("%s %-12s %-30s dur diff %.3f ms  offset diff %.3f ms"
                     % ("OK  " if ok else "FAIL", kind, name[:30], d_dur / 1e6, d_off / 1e6))
    unmatched = len(spans) - len(matched)
    if unmatched:
        lines.append("note: %d stored spans have no NVTX range" % unmatched)
    return lines, len(matched), bad


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _nsys_version():
    # type: () -> Optional[str]
    try:
        out = subprocess.run(["nsys", "--version"], capture_output=True, text=True, timeout=30).stdout
        return out.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def write_row(store, row):
    # type: (str, Dict[str, Any]) -> None
    """One agreement row (only with --write); needs migration e007."""
    con = sqlite3.connect(store)
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='energy_validation_nvtx_agreement'").fetchone():
        raise SystemExit("energy_validation_nvtx_agreement absent: run alems dev migrate --run")
    cols = sorted(row)
    with con:
        con.execute("INSERT INTO energy_validation_nvtx_agreement (%s) VALUES (%s)"
                    % (", ".join(cols), ", ".join("?" * len(cols))), [row[c] for c in cols])


def main(argv=None):
    # type: (list) -> int
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--nsys", required=True, help="Nsight Systems report exported to SQLite")
    p.add_argument("--store", required=True, help="A-LEMS store (read only unless --write)")
    p.add_argument("--run-id", type=int, required=True)
    p.add_argument("--tol-ms", type=float, default=float(os.environ.get("ALEMS_NVTX_TOL_MS") or 1.0))
    p.add_argument("--write", action="store_true", help="store the result (energy_validation_nvtx_agreement)")
    a = p.parse_args(argv)
    spans = stored_spans(a.store, a.run_id)
    row = {"run_id": a.run_id, "nsys_report_path": os.path.abspath(a.nsys), "spans_stored": len(spans),
           "tol_ms": a.tol_ms, "method_id": METHOD_ID, "checked_utc_ns": time.time_ns(),
           "nsys_version": _nsys_version(), "nsys_report_sha256": None, "ranges_found": 0, "matched": 0,
           "missing": len(spans), "outside_tolerance": 0, "max_start_offset_ms": None,
           "max_duration_diff_ms": None}
    if not os.path.isfile(a.nsys):
        sys.stdout.write("trace not found: %s (export it with nsys export --type sqlite)\n" % a.nsys)
        row["status"] = "trace_missing"
        n, bad = 0, 1
    else:
        ranges = nvtx_ranges(a.nsys)
        stats = {}  # type: Dict[str, Any]
        lines, n, bad = compare(spans, ranges, int(a.tol_ms * 1e6), stats)
        sys.stdout.write("\n".join(lines) + "\n")
        sys.stdout.write("%d matched, %d outside %.3f ms\n" % (n, bad, a.tol_ms))
        row.update(stats, nsys_report_sha256=_sha256(a.nsys), ranges_found=len(ranges), matched=n,
                   missing=len(spans) - n, outside_tolerance=bad if n else 0,
                   status="no_ranges" if not n else ("agree" if not bad else "disagree"))
    if a.write:
        write_row(a.store, row)
        sys.stdout.write("stored: %s\n" % row["status"])
    return 0 if n and not bad else 1


if __name__ == "__main__":
    sys.exit(main())
