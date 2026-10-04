#!/usr/bin/env python3
"""
gate_overhead_bench.py: cost of one held log record inside the gate
(39.5.2 WP 2b overhead bound, method of SPEC_39_4 section 3a).

The bound for a run is: held records per window (gate trace, 4th field)
times the per record cost measured here. Measured with the same handler
chain a run entry uses (setup_logging("run")), so filters, the record
factory, and the GatedHandler are all included.

Usage: venv/bin/python scripts/tools/gate_overhead_bench.py [--n 100000] [--repeat 5]
"""

import argparse
import logging
import statistics
import sys
import time
from pathlib import Path

# Run from the engine root without installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.observability import gate, setup_logging  # noqa: E402


def one_pass(log: logging.Logger, n: int, inside: bool) -> float:
    """Return seconds per info() call for n calls, gate closed or open."""
    if inside:
        gate.enter()
    t0 = time.perf_counter()
    for i in range(n):
        log.info("bench record %d", i)  # args kept: formatting is deferred
    dt = time.perf_counter() - t0
    if inside:
        # Discard held records so the open pass does not pay for a flush.
        with gate._lock:
            gate._logs.clear()
        gate.exit()
    return dt / n


def main() -> int:
    """Print median per record cost inside the gate and, for reference, below level."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100000)
    ap.add_argument("--repeat", type=int, default=5)
    a = ap.parse_args()
    setup_logging("run")
    log = logging.getLogger("alems.bench")
    held = [one_pass(log, a.n, True) for _ in range(a.repeat)]
    # A DEBUG call below the root threshold: the floor any logging call pays.
    t0 = time.perf_counter()
    for i in range(a.n):
        log.debug("x %d", i)
    floor = (time.perf_counter() - t0) / a.n
    print("held_record_us_median %.3f" % (statistics.median(held) * 1e6))
    print("held_record_us_all    %s" % " ".join("%.3f" % (x * 1e6) for x in held))
    print("disabled_call_us      %.3f" % (floor * 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
