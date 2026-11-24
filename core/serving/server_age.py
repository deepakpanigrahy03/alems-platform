"""
server_age.py: age of the machine, driver and serving instance at run start.

Why this exists:
    Energy and latency can drift with how long the server has been up (prefix
    cache fill, memory fragmentation, thermal soak). Fresh and warm experiments
    must be told apart per run. All ages are taken at the run start
    (runs.start_time_ns), not at persistence time.

Sources (method server_age_variables, MEASURED):
    time_since_boot_s          /proc/stat btime
    time_since_driver_load_s   NULL: no trustworthy load time on GN100
                               (/proc/driver/nvidia carries the procfs mount
                               time, equal to boot); source recorded as 'none'
    time_since_server_start_s  energy_validation_engine_provenance.server_start_utc
    gpu_busy_observed_since_start_s
                               sum of util_gpu_pct x interval over gpu_samples
                               of runs since the server start in this store;
                               a lower bound (unmeasured time is not seen)
    gpu_temp_c, gpu_sm_clock_mhz, gpu_mem_clock_mhz
                               first gpu_samples row of the run
Reads only, through the connection it is given; never writes (persistence does).
"""

import datetime
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

METHOD_ID = "server_age_variables"
DRIVER_LOAD_SOURCE = "none"


def boot_time_s():
    # type: () -> Optional[float]
    """Boot epoch seconds from /proc/stat btime; None off Linux."""
    try:
        with open("/proc/stat") as fh:
            return float(next(l.split()[1] for l in fh if l.startswith("btime")))
    except (OSError, StopIteration, ValueError, IndexError) as exc:
        logger.debug("server age: btime unavailable: %s", exc)
        return None


def _iso_to_s(text):
    # type: (Optional[str]) -> Optional[float]
    """ISO UTC (ms, Z) to epoch seconds."""
    if not text:
        return None
    try:
        ts = datetime.datetime.strptime(text.rstrip("Z")[:23], "%Y-%m-%dT%H:%M:%S.%f")
    except ValueError:
        return None
    return ts.replace(tzinfo=datetime.timezone.utc).timestamp()


def _server(conn, exp_id):
    # type: (Any, int) -> tuple
    """(server_start_s, server_instance_id) of the experiment's latest matched instance."""
    try:
        row = conn.execute(
            "SELECT server_start_utc, server_instance_id FROM energy_validation_engine_provenance "
            "WHERE exp_id = ? AND server_pid IS NOT NULL ORDER BY provenance_id DESC LIMIT 1",
            (exp_id,)).fetchone()
    except Exception as exc:  # table absent: extension migration not applied
        logger.debug("server age: no engine provenance: %s", exc)
        return None, None
    return (_iso_to_s(row[0]), row[1]) if row else (None, None)


def _gpu_busy_observed(conn, start_ns, run_start_ns):
    # type: (Any, int, int) -> Optional[float]
    """GPU busy seconds in A-LEMS samples between server start and run start."""
    row = conn.execute(
        "SELECT SUM(g.interval_ns * g.util_gpu_pct / 100.0) / 1e9, COUNT(*) FROM gpu_samples g "
        "WHERE g.sample_start_ns >= ? AND g.sample_start_ns < ? AND g.util_gpu_pct IS NOT NULL",
        (start_ns, run_start_ns)).fetchone()
    return row[0] if row and row[1] else None   # no samples: unknown, not 0


def _first_gpu_sample(conn, run_id):
    # type: (Any, int) -> tuple
    """(temperature_c, sm_clock_mhz, mem_clock_mhz) of the run's first GPU sample."""
    row = conn.execute(
        "SELECT temperature_c, sm_clock_mhz, mem_clock_mhz FROM gpu_samples "
        "WHERE run_id = ? ORDER BY sample_start_ns LIMIT 1", (run_id,)).fetchone()
    return tuple(row) if row else (None, None, None)


def compute(conn, run_id, exp_id):
    # type: (Any, int, int) -> Optional[Dict[str, Any]]
    """
    Age variables of one run; None when the run has no start time.

    Args:
        conn:   read connection of the store (persistence passes its own).
        run_id: run being persisted.
        exp_id: its experiment.
    """
    row = conn.execute("SELECT start_time_ns FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if not row or row[0] is None:
        return None
    run_start_ns = int(row[0])
    run_start_s = run_start_ns / 1e9
    boot = boot_time_s()
    srv_start, instance = _server(conn, exp_id)
    temp, sm, mem = _first_gpu_sample(conn, run_id)
    return {
        "run_id": run_id, "exp_id": exp_id, "run_start_ns": run_start_ns,
        "time_since_boot_s": run_start_s - boot if boot else None,
        "time_since_driver_load_s": None, "driver_load_source": DRIVER_LOAD_SOURCE,
        "time_since_server_start_s": run_start_s - srv_start if srv_start else None,
        "server_instance_id": instance,
        "gpu_busy_observed_since_start_s":
            _gpu_busy_observed(conn, int(srv_start * 1e9), run_start_ns) if srv_start else None,
        "gpu_temp_c": temp, "gpu_sm_clock_mhz": sm, "gpu_mem_clock_mhz": mem,
        "method_id": METHOD_ID,
    }
