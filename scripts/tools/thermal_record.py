#!/usr/bin/env python3
"""
Record between-runs thermal data for N seconds with no workload.

Opens a continuous recording, records for --seconds, closes it, and prints
rows per table, achieved rates, maximum gaps and the session checks. Used for
the idle soak (stability test) and as a smoke test of the recorder.

Usage:
    python scripts/tools/thermal_record.py --seconds 60
    python scripts/tools/thermal_record.py --recover      # mark stale sessions aborted
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.recording import session as rec_session          # noqa: E402

logger = logging.getLogger("thermal_record")


def _summary(writer_factory, sid: int) -> None:
    """Print rows per te table and the session checks for one session."""
    w = writer_factory()
    w.open()
    try:
        for table in ("te_thermal_samples", "te_cooling_samples", "te_gpu_temp_samples",
                      "te_power_rail_samples", "te_ambient_samples", "te_clock_anchor"):
            n = w.execute("SELECT COUNT(*) AS n FROM %s WHERE session_id = ?" % table,
                          (sid,))[0]["n"]
            print("%-24s %8d rows" % (table, n))
        s = w.execute("SELECT status, start_ns, end_ns, cooldown_status FROM te_session "
                      "WHERE session_id = ?", (sid,))[0]
        print("session %d: status %s, %.1f s, cool-down %s" % (
            sid, s["status"], (s["end_ns"] - s["start_ns"]) / 1e9, s["cooldown_status"]))
        for c in w.execute("SELECT check_name, result, value, threshold, note FROM "
                           "te_session_check WHERE session_id = ? ORDER BY check_name",
                           (sid,)):
            print("  %-24s %-5s value=%s limit=%s %s" % (
                c["check_name"], c["result"], c["value"], c["threshold"], c["note"] or ""))
    finally:
        w.close()


def main() -> int:
    """Parse arguments, record, print the summary. Returns the exit code."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--recover", action="store_true", help="only mark stale sessions aborted")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    wf = rec_session._writer_factory()
    if a.recover:
        from extensions.thermal_exposure.recorder import recover_open_sessions
        print("sessions marked aborted:", recover_open_sessions(wf, "local"))
        return 0
    rec = rec_session.open_recording(pre_window_s=0.0)
    t_end = time.monotonic() + a.seconds
    while time.monotonic() < t_end:
        time.sleep(min(30.0, max(0.0, t_end - time.monotonic())))
        logger.info("recording: %.0f s of %.0f s", a.seconds - (t_end - time.monotonic()),
                    a.seconds)
    rec.close_and_cleanup()
    _summary(wf, rec.session_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
