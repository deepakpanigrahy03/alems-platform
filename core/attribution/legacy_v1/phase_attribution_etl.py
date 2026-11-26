"""
phase_attribution_etl.py — Phase Attribution ETL v2: Direct Sample Measurement.

METHODOLOGY CHANGE (v1 -> v2):
    v1: Weighted allocation — forces SUM(phases) = attributed by construction.
        Reviewer attack: "Your phase sums are normalized to attributed by design.
        This tells us nothing about whether phase boundaries captured real energy."

    v2: Direct sample measurement — each phase energy measured independently
        from hardware energy samples within phase timestamp windows.
        E_phase_i = raw_energy_i x cpu_frac_i  (direct, not normalized)
        inter_phase_energy_uj = attributed - SUM(E_phase_i)  (real residual)

        Reviewer defence: "Phase energies are independently measured from hardware
        samples. The inter-phase residual (Python overhead, tool dispatch,
        framework calls between phase boundaries) is explicitly captured and
        reported. No energy is hidden by normalization."

PLATFORM SUPPORT (PAC-4 compliant):
    x86 / UBUNTU2505 (RAPL):
        Source: energy_samples table (pkg_start_uj, pkg_end_uj, sample_start_ns)
        Method: _direct_phase_energy() — unchanged from original v2

    ARM / GN100 (SPBM):
        Source: energy_sample_domains JOIN energy_samples_v2 (via sample_id)
        Domain: domain_id=1 (PACKAGE) from energy_domains table
        Method: _direct_phase_energy_spbm() — added in P2-ETL-01
        Timestamps: energy_samples_v2.timestamp_ns used for phase windowing
        Energy: energy_sample_domains.energy_uj summed within window

    Platform is detected automatically via _get_cpu_package_domain_id().
    If energy_sample_domains has no PACKAGE domain rows, falls back to
    RAPL path — never raises, never silently corrupts. (Rule DC-3, PAC-4)

FORMULA (v2):
    raw_energy_i    = SUM(pkg_end_uj - pkg_start_uj) for samples in phase window
                      [x86] or SUM(energy_uj) for domain samples in window [ARM]
    cpu_frac_i      = (MAX(proc_ticks_end) - MIN(proc_ticks_start)) /
                      (MAX(total_ticks_end) - MIN(total_ticks_start))
    E_phase_i       = raw_energy_i x cpu_frac_i
    inter_phase_uj  = attributed - SUM(E_phase_i)
                      (positive = energy between phase boundaries)
                      (negative = measurement overlap — logged as warning)

PROVENANCE:
    E_phase_i          MEASURED   (direct from hardware samples x /proc ticks)
    inter_phase_energy CALCULATED (arithmetic residual)
    fallback path      INFERRED   (run-level cpu_fraction when phase ticks absent)

DATA SOURCES (x86):
    energy_samples       : pkg_start_uj, pkg_end_uj, sample_start_ns, sample_end_ns
    interrupt_samples    : proc_ticks_start/end, total_ticks_start/end
    orchestration_events : start_time_ns, end_time_ns per phase
    runs                 : attributed_energy_uj (run-level process share)

DATA SOURCES (ARM/GN100):
    energy_sample_domains : sample_id, domain_id, energy_uj
    energy_samples_v2     : sample_id, timestamp_ns, interval_ns
    interrupt_samples     : proc_ticks_start/end, total_ticks_start/end
    orchestration_events  : start_time_ns, end_time_ns per phase
    runs                  : attributed_energy_uj (run-level process share)

COMPATIBILITY:
    v1 runs: unaffected — v2 only runs for new runs and explicit backfill.
    orchestration_events: attribution_method column records 'sample_direct_v2'
    runs: phase_sample_coverage_pct column records measurement quality.
    x86 platforms: zero change to existing code path (PAC-4).

Run modes:
    Async per-run : process_run_async(run_id)         called from experiment_runner
    Manual        : python scripts/etl/phase_attribution_etl.py --run-id <id>
    Backfill      : python scripts/etl/phase_attribution_etl.py --backfill-all
"""

import argparse
import logging
import sqlite3
import threading
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

from core.storage.resolver import caller_store as get_alems_db_path
DB_PATH = get_alems_db_path()
PHASES  = ("planning", "execution", "synthesis")

# Method version — bump when formula changes
ATTRIBUTION_METHOD_V2       = "sample_direct_v2"
ATTRIBUTION_METHOD_FALLBACK = "fallback_run_level_v2"

# Domain name used to identify the CPU package channel on SPBM platforms.
# On GN100: domain_id=1, name='PACKAGE' confirmed 2026-06-28.
# Read from energy_domains at runtime — never hardcoded (Rule CQC-6).
_CPU_PACKAGE_DOMAIN_NAME = "PACKAGE"


def _conn(db_path, existing=None):
    if existing is not None:
        return existing
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _get_cpu_package_domain_id(conn):
    # type: (sqlite3.Connection) -> Optional[int]
    """
    Look up the CPU package domain_id from energy_domains table.

    Used to detect SPBM platform (ARM/GN100) vs RAPL platform (x86).
    Returns domain_id for the PACKAGE channel, or None if not present
    (indicating non-SPBM platform — not an error, triggers RAPL path).

    On GN100: returns 1 (PACKAGE domain, SPBM silicon accumulator).
    On UBUNTU2505: energy_domains may not have PACKAGE rows with data,
    returns None, x86 RAPL path is used unchanged.

    Rule CQC-6: domain name read from DB, never hardcoded as integer.
    """
    row = conn.execute(
        "SELECT domain_id FROM energy_domains WHERE name = ? LIMIT 1",
        (_CPU_PACKAGE_DOMAIN_NAME,)
    ).fetchone()
    if row is None:
        return None
    # Confirm this domain actually has rows in energy_sample_domains.
    # A domain_id existing in energy_domains but having zero sample rows
    # means this is not a SPBM platform — use RAPL path instead.
    count_row = conn.execute(
        "SELECT COUNT(*) as n FROM energy_sample_domains WHERE domain_id = ? LIMIT 1",
        (row[0],)
    ).fetchone()
    if not count_row or count_row["n"] == 0:
        return None
    return row[0]


def _get_phases(conn, run_id):
    # type: (sqlite3.Connection, int) -> list
    """
    Fetch all phase events for this run from orchestration_events.
    Returns list of rows with phase, start_time_ns, end_time_ns.
    """
    return conn.execute("""
        SELECT event_id, phase, start_time_ns, end_time_ns
        FROM orchestration_events
        WHERE run_id = ? AND phase IN ('planning','execution','synthesis')
        ORDER BY start_time_ns
    """, (run_id,)).fetchall()


def _direct_phase_energy(conn, run_id, start_ns, end_ns):
    # type: (sqlite3.Connection, int, int, int) -> Tuple[int, int]
    """
    Compute phase energy directly from RAPL energy_samples (x86 path).

    v2 method: SUM of per-interval deltas within phase window.
    Each sample stores pkg_end_uj - pkg_start_uj as the interval delta.
    Summing gives total package energy for that phase window.

    This is direct measurement — no allocation, no normalization.
    A sample is included if its measurement window falls within the phase window.

    Bug 14 fix: proportional bracketing for sub-sample phases.
    When no samples fall entirely inside the phase window (phase shorter than
    sample interval), find the bracketing sample that overlaps the window and
    allocate energy proportional to the overlap fraction.
    Provenance: energy value is MEASURED; time-slice fraction is CALCULATED.
    Accuracy bound: assumes flat power draw within the 100ms bracketing interval.

    Args:
        conn:     DB connection
        run_id:   run to query
        start_ns: phase start timestamp (ns)
        end_ns:   phase end timestamp (ns)

    Returns:
        (energy_uj, sample_count) — energy_uj=0 if no samples in window
    """
    row = conn.execute("""
        SELECT
            SUM(pkg_end_uj - pkg_start_uj) AS interval_sum,
            COUNT(*)                        AS n_samples
        FROM energy_samples
        WHERE run_id        = ?
          AND sample_start_ns >= ?
          AND sample_end_ns   <= ?
          AND pkg_end_uj IS NOT NULL
          AND pkg_start_uj IS NOT NULL
    """, (run_id, start_ns, end_ns)).fetchone()

    if row and row["interval_sum"] is not None and row["n_samples"] > 0:
        # At least one full sample inside the window — direct measurement path.
        energy = max(0, row["interval_sum"])
        return energy, row["n_samples"]

    # Bug 14 fix: no full samples inside window — find the bracketing interval.
    # The bracketing sample is the one whose window overlaps [start_ns, end_ns].
    # We take the sample with the largest overlap with the phase window.
    bracket = conn.execute("""
        SELECT
            pkg_start_uj,
            pkg_end_uj,
            sample_start_ns,
            sample_end_ns
        FROM energy_samples
        WHERE run_id        = ?
          AND sample_start_ns < ?
          AND sample_end_ns   > ?
          AND pkg_end_uj IS NOT NULL
          AND pkg_start_uj IS NOT NULL
        ORDER BY
            MIN(sample_end_ns, ?) - MAX(sample_start_ns, ?) DESC
        LIMIT 1
    """, (run_id, end_ns, start_ns, end_ns, start_ns)).fetchone()

    if not bracket:
        return 0, 0

    interval_ns   = bracket["sample_end_ns"] - bracket["sample_start_ns"]
    if interval_ns <= 0:
        return 0, 0

    # Overlap of phase window with bracketing sample interval.
    overlap_ns    = min(bracket["sample_end_ns"], end_ns) - max(bracket["sample_start_ns"], start_ns)
    overlap_frac  = overlap_ns / interval_ns

    interval_energy_uj = max(0, bracket["pkg_end_uj"] - bracket["pkg_start_uj"])
    # Proportional slice: phase gets its time-fraction of the interval energy.
    energy = int(interval_energy_uj * overlap_frac)

    logger.debug(
        "_direct_phase_energy: proportional bracket run=%d "
        "phase=[%d,%d] bracket=[%d,%d] overlap_frac=%.4f energy_uj=%d",
        run_id, start_ns, end_ns,
        bracket["sample_start_ns"], bracket["sample_end_ns"],
        overlap_frac, energy,
    )
    # Return sample_count=0 so caller quality score stays conservative (0.5).
    return energy, 0


def _direct_phase_energy_spbm(conn, run_id, start_ns, end_ns, cpu_domain_id):
    # type: (sqlite3.Connection, int, int, int, int) -> Tuple[int, int]
    """
    Compute phase energy from energy_sample_domains (ARM/SPBM path).

    Used on GN100 where energy is stored in energy_sample_domains keyed
    by domain_id, not in energy_samples (RAPL/x86 only).

    energy_sample_domains has no timestamp column. Timestamps come from
    energy_samples_v2, joined via sample_id (confirmed 2026-06-28).

    A sample is included if its timestamp_ns falls within [start_ns, end_ns].
    energy_uj is the SPBM silicon accumulator reading for that sample interval.

    Provenance: MEASURED (direct from SPBM silicon accumulator, domain PACKAGE).
    cpu_fraction is applied by the caller in compute_phase_attribution().

    Args:
        conn:          Open DB connection
        run_id:        runs.run_id
        start_ns:      Phase start timestamp (nanoseconds)
        end_ns:        Phase end timestamp (nanoseconds)
        cpu_domain_id: energy_domains.domain_id for CPU PACKAGE channel

    Returns:
        (energy_uj, sample_count) — energy_uj=0 if no samples in window
    """
    row = conn.execute("""
        SELECT
            COALESCE(SUM(esd.energy_uj), 0) AS interval_sum,
            COUNT(*)                          AS n_samples
        FROM energy_sample_domains esd
        JOIN energy_samples_v2 esv ON esv.sample_id = esd.sample_id
        WHERE esd.run_id    = ?
          AND esd.domain_id = ?
          AND esv.timestamp_ns >= ?
          AND esv.timestamp_ns <= ?
    """, (run_id, cpu_domain_id, start_ns, end_ns)).fetchone()

    if row and row["n_samples"] > 0:
        # At least one SPBM sample inside the window — direct measurement path.
        energy = max(0, int(row["interval_sum"]))
        return energy, row["n_samples"]

    # Bug 14 fix: no SPBM samples inside window — proportional bracketing.
    # energy_samples_v2.timestamp_ns is the sample midpoint (SPBM convention).
    # interval_ns gives the sample duration — used to reconstruct the window.
    # We find the sample whose window [t - interval/2, t + interval/2] overlaps
    # the phase window, then allocate its energy proportional to overlap fraction.
    bracket = conn.execute("""
        SELECT
            esd.energy_uj         AS sample_energy_uj,
            esv.timestamp_ns      AS ts,
            esv.interval_ns       AS interval_ns
        FROM energy_sample_domains esd
        JOIN energy_samples_v2 esv ON esv.sample_id = esd.sample_id
        WHERE esd.run_id    = ?
          AND esd.domain_id = ?
          AND esv.timestamp_ns - (esv.interval_ns / 2) < ?
          AND esv.timestamp_ns + (esv.interval_ns / 2) > ?
          AND esd.energy_uj IS NOT NULL
          AND esd.energy_uj > 0
        ORDER BY
            MIN(esv.timestamp_ns + (esv.interval_ns / 2), ?)
            - MAX(esv.timestamp_ns - (esv.interval_ns / 2), ?) DESC
        LIMIT 1
    """, (run_id, cpu_domain_id, end_ns, start_ns, end_ns, start_ns)).fetchone()

    if not bracket or not bracket["interval_ns"]:
        return 0, 0

    half_ns      = bracket["interval_ns"] // 2
    sample_start = bracket["ts"] - half_ns
    sample_end   = bracket["ts"] + half_ns
    interval_ns  = sample_end - sample_start
    if interval_ns <= 0:
        return 0, 0

    overlap_ns   = min(sample_end, end_ns) - max(sample_start, start_ns)
    overlap_frac = overlap_ns / interval_ns

    energy = int(max(0, bracket["sample_energy_uj"]) * overlap_frac)

    logger.debug(
        "_direct_phase_energy_spbm: proportional bracket run=%d "
        "phase=[%d,%d] sample_ts=%d interval_ns=%d overlap_frac=%.4f energy_uj=%d",
        run_id, start_ns, end_ns,
        bracket["ts"], bracket["interval_ns"],
        overlap_frac, energy,
    )
    # Return sample_count=0 so caller quality score stays conservative (0.5).
    return energy, 0

def _phase_cpu_fraction(conn, run_id, start_ns, end_ns):
    # type: (sqlite3.Connection, int, int, int) -> Tuple[Optional[float], int, int, int]
    """
    Counter delta method for process CPU fraction within phase window.

    Uses MAX-MIN on proc_ticks and total_ticks within phase timestamp bounds.
    Returns (cpu_fraction, proc_delta, total_delta, n_samples).
    cpu_fraction is None when insufficient interrupt_samples data.
    """
    row = conn.execute("""
        SELECT
            MIN(proc_ticks_start)  AS proc_min,
            MAX(proc_ticks_end)    AS proc_max,
            MIN(total_ticks_start) AS total_min,
            MAX(total_ticks_end)   AS total_max,
            COUNT(*)               AS n_samples
        FROM interrupt_samples
        WHERE run_id        = ?
          AND sample_start_ns >= ?
          AND sample_end_ns   <= ?
    """, (run_id, start_ns, end_ns)).fetchone()

    if not row or row["proc_min"] is None or row["total_min"] is None:
        return None, 0, 0, row["n_samples"] if row else 0

    proc_delta  = row["proc_max"]  - row["proc_min"]
    total_delta = row["total_max"] - row["total_min"]

    if total_delta <= 0:
        return None, proc_delta, total_delta, row["n_samples"]

    frac = max(0.0, min(1.0, proc_delta / total_delta))
    return frac, proc_delta, total_delta, row["n_samples"]


def _fallback_cpu_fraction(conn, run_id):
    # type: (sqlite3.Connection, int) -> float
    """
    Run-level cpu_fraction fallback when phase-level ticks unavailable.
    Provenance: INFERRED (run-level proxy for phase-level measurement).
    """
    row = conn.execute(
        "SELECT cpu_fraction FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if not row or row["cpu_fraction"] is None:
        return 0.0
    return float(row["cpu_fraction"])


def _total_run_samples(conn, run_id, use_spbm, cpu_domain_id):
    # type: (sqlite3.Connection, int, bool, Optional[int]) -> int
    """
    Total energy sample rows for this run — used for coverage computation.

    Uses energy_sample_domains for SPBM platforms, energy_samples for RAPL.
    Coverage denominator must match the source used for phase measurement.
    """
    if use_spbm and cpu_domain_id is not None:
        # Count SPBM PACKAGE domain samples for this run
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM energy_sample_domains "
            "WHERE run_id = ? AND domain_id = ?",
            (run_id, cpu_domain_id)
        ).fetchone()
    else:
        # Count RAPL energy_samples for this run
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM energy_samples WHERE run_id = ?",
            (run_id,)
        ).fetchone()
    return row["n"] if row else 0


ATTRIBUTION_METHOD_V3       = "sample_direct_v3"
ATTRIBUTION_METHOD_V3_FALLBACK = "run_share_v3"


def _has_column(conn, table, column):
    # type: (sqlite3.Connection, str, str) -> bool
    return any(r[1] == column for r in conn.execute("PRAGMA table_info(%s)" % table))


def busy_share(conn, run_id, start_ns, end_ns):
    # type: (sqlite3.Connection, int, int, int) -> Tuple[Optional[float], Optional[str], int]
    """
    Process share of user+nice+system ticks inside [start_ns, end_ns] (v3).

    Same denominator as runs.cpu_fraction (read_total_cpu_ticks). Sums per
    sample deltas, so idle ticks never enter. Rows without nice ticks
    (before v129) use user+system and say so in the returned basis.

    Returns (share or None, basis 'user_nice_system' | 'user_system' | None, n_samples).
    share is None when process ticks are unknown or the denominator is not positive.
    """
    nice = ("COALESCE(nice_ticks_end - nice_ticks_start, 0)"
            if _has_column(conn, "interrupt_samples", "nice_ticks_start") else "0")
    row = conn.execute("""
        SELECT SUM(proc_ticks_end - proc_ticks_start)                         AS d_proc,
               COUNT(proc_ticks_start)                                        AS n_proc,
               SUM((user_ticks_end - user_ticks_start)
                   + (system_ticks_end - system_ticks_start) + %s)            AS d_busy,
               SUM(CASE WHEN %s THEN 1 ELSE 0 END)                            AS n_nice,
               COUNT(*)                                                       AS n
        FROM interrupt_samples
        WHERE run_id = ? AND sample_start_ns >= ? AND sample_end_ns <= ?
    """ % (nice, "nice_ticks_start IS NOT NULL"
           if _has_column(conn, "interrupt_samples", "nice_ticks_start") else "0"),
        (run_id, start_ns, end_ns)).fetchone()
    n = row["n"] or 0
    if n == 0 or not row["n_proc"] or row["n_proc"] < n or row["d_proc"] is None:
        return None, None, n
    if row["d_busy"] is None or row["d_busy"] <= 0 or row["d_proc"] < 0:
        return None, None, n
    basis = "user_nice_system" if row["n_nice"] == n else "user_system"
    return max(0.0, min(1.0, row["d_proc"] / float(row["d_busy"]))), basis, n


def _task_window(run):
    # type: (sqlite3.Row) -> Optional[Tuple[int, int]]
    """[t0, t1] from the run row (same rule as run_persistence._insert_task_events)."""
    if run["start_time_ns"] is None or run["pre_task_duration_ns"] is None \
            or run["task_duration_ns"] is None:
        return None
    t0 = run["start_time_ns"] + run["pre_task_duration_ns"]
    return t0, t0 + run["task_duration_ns"]


def _gaps(window, intervals):
    # type: (Tuple[int, int], list) -> list
    """Complement of the union of intervals inside window (nested work counted once)."""
    lo, hi = window
    merged = []
    for s, e in sorted((max(lo, s), min(hi, e)) for s, e in intervals):
        if e <= s:
            continue
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    out, cur = [], lo
    for s, e in merged:
        if s > cur:
            out.append((cur, s))
        cur = max(cur, e)
    if cur < hi:
        out.append((cur, hi))
    return out


def _covered_ns(conn, run_id, start_ns, end_ns, use_spbm, cpu_domain_id):
    # type: (sqlite3.Connection, int, int, int, bool, Optional[int]) -> int
    """Sum of sample intervals counted inside the window (same filter as the raw energy)."""
    if use_spbm:
        row = conn.execute("""
            SELECT COALESCE(SUM(esv.interval_ns), 0) FROM energy_sample_domains esd
            JOIN energy_samples_v2 esv ON esv.sample_id = esd.sample_id
            WHERE esd.run_id = ? AND esd.domain_id = ?
              AND esv.timestamp_ns >= ? AND esv.timestamp_ns <= ?
        """, (run_id, cpu_domain_id, start_ns, end_ns)).fetchone()
    else:
        row = conn.execute("""
            SELECT COALESCE(SUM(sample_end_ns - sample_start_ns), 0) FROM energy_samples
            WHERE run_id = ? AND sample_start_ns >= ? AND sample_end_ns <= ?
              AND pkg_end_uj IS NOT NULL AND pkg_start_uj IS NOT NULL
        """, (run_id, start_ns, end_ns)).fetchone()
    return int(row[0] or 0)


def _window_estimate(conn, run_id, start_ns, end_ns, use_spbm, cpu_domain_id,
                     p_base_w, run_share):
    # type: (...) -> dict
    """
    Local attributed estimate for one window: share x (raw - P_baseline x duration).
    Fallback to the run share (INFERRED) when the window has no tick data.
    energy None when unknown (MIC-1).
    """
    if use_spbm:
        raw, n_e = _direct_phase_energy_spbm(conn, run_id, start_ns, end_ns, cpu_domain_id)
    else:
        raw, n_e = _direct_phase_energy(conn, run_id, start_ns, end_ns)
    share, basis, n_t = busy_share(conn, run_id, start_ns, end_ns)
    if share is not None:
        method, quality = ATTRIBUTION_METHOD_V3, (min(1.0, n_e / 10.0) if n_e > 0 else 0.5)
    elif run_share is not None:
        share, basis, method, quality = run_share, "run_cpu_fraction", ATTRIBUTION_METHOD_V3_FALLBACK, 0.3
    else:
        method, quality = None, None
    # Baseline only over the time the energy samples cover: a window part
    # without samples is unknown, never negative energy (it stays in the residual).
    covered_ns = _covered_ns(conn, run_id, start_ns, end_ns, use_spbm, cpu_domain_id) \
        if n_e > 0 else (end_ns - start_ns if raw else 0)
    dynamic = None
    if raw is not None and p_base_w is not None:
        dynamic = raw - p_base_w * covered_ns / 1e3   # W x ns / 1e3 = uJ
    energy = int(round(share * dynamic)) if share is not None and dynamic is not None else None
    return {"raw": raw, "dynamic": dynamic, "share": share, "basis": basis,
            "energy": energy, "method": method, "quality": quality, "n_energy": n_e,
            "uncovered_ns": max(0, (end_ns - start_ns) - covered_ns)}


def compute_phase_attribution(run_id, db_path=DB_PATH, conn=None):
    # type: (int, str, object) -> dict
    """
    Phase attribution v3 (claude/DESIGN_45_phase_attribution.md).

    One basis with the run level: share = d(proc ticks) / d(user+nice+system
    ticks) and energy = dynamic (raw minus baseline power x duration, the same
    baseline that produced runs.dynamic_energy_uj). Each phase and each real gap
    inside [t0, t1] is a local estimate. inter_phase is the energy of the gaps.
    The signed residual (run attributed minus all local estimates) is returned
    and stored by the attribution ETL; it is never clamped. Unknown is NULL.
    """
    _own_conn = conn is None
    conn = _conn(db_path, existing=conn)
    _prev_factory = conn.row_factory
    conn.row_factory = sqlite3.Row   # restored before return (shared writer connection)
    try:
        return _compute_v3(conn, run_id, _own_conn)
    finally:
        if not _own_conn:
            conn.row_factory = _prev_factory


def _compute_v3(conn, run_id, _own_conn):
    # type: (sqlite3.Connection, int, bool) -> dict
    """Body of compute_phase_attribution (v3)."""
    cpu_domain_id = _get_cpu_package_domain_id(conn)
    use_spbm = cpu_domain_id is not None

    phases = _get_phases(conn, run_id)
    if not phases:
        if _own_conn:
            conn.close()
        return {"error": "no phases for run %d" % run_id}
    run = conn.execute(
        "SELECT attributed_energy_uj, baseline_energy_uj, duration_ns, cpu_fraction, "
        "start_time_ns, pre_task_duration_ns, task_duration_ns FROM runs WHERE run_id = ?",
        (run_id,)).fetchone()
    if not run:
        if _own_conn:
            conn.close()
        return {"error": "run %d not found" % run_id}

    # Baseline power over the run's energy window (duration_ns; equals the task
    # window for measurement_boundary_v2 runs).
    p_base_w = (run["baseline_energy_uj"] / run["duration_ns"] * 1e3
                if run["baseline_energy_uj"] is not None and run["duration_ns"] else None)
    run_share = run["cpu_fraction"]
    window = _task_window(run) or (min(p["start_time_ns"] for p in phases),
                                   max(p["end_time_ns"] for p in phases))

    totals = {p: 0 for p in PHASES}
    unknown = set()
    n_direct = n_fallback = 0
    for ph in phases:
        est = _window_estimate(conn, run_id, ph["start_time_ns"], ph["end_time_ns"],
                               use_spbm, cpu_domain_id, p_base_w, run_share)
        conn.execute("""
            UPDATE orchestration_events
            SET raw_energy_uj = ?, cpu_fraction_per_phase = ?, attributed_energy_uj = ?,
                attribution_method = ?, quality_score = ?
            WHERE event_id = ?
        """, (est["raw"], est["share"], est["energy"], est["method"], est["quality"],
              ph["event_id"]))
        if est["energy"] is None:
            unknown.add(ph["phase"])
        else:
            totals[ph["phase"]] += est["energy"]
        n_direct += est["method"] == ATTRIBUTION_METHOD_V3
        n_fallback += est["method"] == ATTRIBUTION_METHOD_V3_FALLBACK
        if est["method"] == ATTRIBUTION_METHOD_V3_FALLBACK:
            logger.info("phase_attribution v3: run=%d phase=%s no tick data, run share %.4f (INFERRED)",
                        run_id, ph["phase"], run_share)

    inter, inter_ns, uncovered_ns = 0, 0, 0
    for s, e in _gaps(window, [(p["start_time_ns"], p["end_time_ns"]) for p in phases]):
        inter_ns += e - s
        est = _window_estimate(conn, run_id, s, e, use_spbm, cpu_domain_id, p_base_w, run_share)
        uncovered_ns += est["uncovered_ns"]
        if est["energy"] is None:
            inter = None
            break
        inter += est["energy"]

    phase_vals = {p: (None if p in unknown else totals[p]) for p in PHASES}
    parts = list(phase_vals.values()) + [inter, run["attributed_energy_uj"]]
    residual = (run["attributed_energy_uj"] - sum(parts[:4])) if None not in parts else None

    conn.execute("""
        UPDATE runs SET planning_energy_uj = ?, execution_energy_uj = ?,
               synthesis_energy_uj = ?, inter_phase_energy_uj = ?
        WHERE run_id = ?
    """, (phase_vals["planning"], phase_vals["execution"], phase_vals["synthesis"],
          inter, run_id))
    conn.commit()
    if _own_conn:
        conn.close()
    return {"planning": phase_vals["planning"], "execution": phase_vals["execution"],
            "synthesis": phase_vals["synthesis"], "inter_phase_uj": inter,
            "inter_phase_ns": inter_ns, "uncovered_ns": uncovered_ns, "residual_uj": residual,
            "attributed": run["attributed_energy_uj"],
            "n_direct": n_direct, "n_fallback": n_fallback}


def phase_residual(conn, run_id):
    # type: (sqlite3.Connection, int) -> Optional[int]
    """Signed residual from the stored run row (used by the attribution ETL)."""
    r = conn.execute("SELECT attributed_energy_uj, planning_energy_uj, execution_energy_uj, "
                     "synthesis_energy_uj, inter_phase_energy_uj FROM runs WHERE run_id = ?",
                     (run_id,)).fetchone()
    if r is None or any(v is None for v in r):
        return None
    return r[0] - (r[1] + r[2] + r[3] + r[4])


def process_run_async(run_id, db_path=DB_PATH):
    # type: (int, str) -> None
    """
    Non-blocking background thread — called from experiment_runner after save_pair.
    Also called from run_experiment.py after execute_goal completes.
    """
    t = threading.Thread(
        target=compute_phase_attribution,
        args=(run_id, db_path),
        daemon=True,
    )
    t.start()


def backfill_all(db_path=DB_PATH):
    # type: (str) -> None
    """
    Backfill all agentic runs with v2 phase attribution.
    Safe to rerun — idempotent (UPDATE not INSERT).
    Works on both x86 (RAPL) and ARM (SPBM) databases.
    Platform is detected automatically per run.
    """
    conn = _conn(db_path)
    runs = conn.execute(
        "SELECT run_id FROM runs WHERE workflow_type='agentic' ORDER BY run_id"
    ).fetchall()
    conn.close()

    print("Backfilling %d agentic runs with phase_attribution v2..." % len(runs))
    ok = err = 0
    for row in runs:
        result = compute_phase_attribution(row["run_id"], db_path)
        if "error" in result:
            print("  run %d: SKIP — %s" % (row["run_id"], result["error"]))
            err += 1
        else:
            print(
                "  run %d: plan=%s exec=%s synth=%s inter=%s residual=%s" % (
                    row["run_id"], result["planning"], result["execution"],
                    result["synthesis"], result["inter_phase_uj"], result["residual_uj"],
                )
            )
            ok += 1

    print("Done: %d ok, %d skipped." % (ok, err))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Phase attribution ETL v2 — direct sample measurement"
    )
    parser.add_argument("--run-id",       type=int, help="Process single run")
    parser.add_argument("--backfill-all", action="store_true",
                        help="Reprocess all agentic runs")
    parser.add_argument("--db",           default=DB_PATH)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    if args.backfill_all:
        backfill_all(args.db)
    elif args.run_id:
        result = compute_phase_attribution(args.run_id, args.db)
        print(result)
    else:
        parser.print_help()
