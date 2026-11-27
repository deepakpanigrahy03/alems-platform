#!/usr/bin/env python3
"""
test_exp_integrity.py — Experiment integrity scanner.

Accepts an exp_id and walks all child tables, reporting pass/fail
per table with column-level checks. Run after every experiment to
verify all 12 tables are correctly populated.

Usage:
    python scripts/test_exp_integrity.py --exp-id 721
    python scripts/test_exp_integrity.py --latest
    python scripts/test_exp_integrity.py --latest --experiment-type failure_injection
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.tools.path_loader import get_alems_db_path
DB_PATH = get_alems_db_path()

# ANSI colors
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


def ok(msg):  return f"{GREEN}✅ {msg}{RESET}"
def fail(msg): return f"{RED}❌ {msg}{RESET}"
def warn(msg): return f"{YELLOW}⚠️  {msg}{RESET}"


def check_experiment(conn, exp_id: int) -> dict:
    """Load experiment row and basic metadata."""
    row = conn.execute(
        "SELECT exp_id, experiment_type, workflow_type, status, "
        "runs_completed, runs_total FROM experiments WHERE exp_id = ?",
        (exp_id,)
    ).fetchone()
    if row is None:
        print(fail(f"exp_id={exp_id} not found in experiments table"))
        sys.exit(1)
    return dict(row)


def get_run_ids(conn, exp_id: int) -> list:
    rows = conn.execute(
        "SELECT run_id FROM runs WHERE exp_id = ?", (exp_id,)
    ).fetchall()
    return [r[0] for r in rows]


def get_goal_ids(conn, exp_id: int) -> list:
    rows = conn.execute(
        "SELECT goal_id FROM goal_execution WHERE exp_id = ?", (exp_id,)
    ).fetchall()
    return [r[0] for r in rows]


def get_attempt_ids(conn, goal_ids: list) -> list:
    if not goal_ids:
        return []
    placeholders = ",".join("?" * len(goal_ids))
    rows = conn.execute(
        f"SELECT attempt_id FROM goal_attempt WHERE goal_id IN ({placeholders})",
        goal_ids
    ).fetchall()
    return [r[0] for r in rows]


def check_runs(conn, exp_id: int, run_ids: list, exp_meta: dict) -> list:
    results = []
    count = len(run_ids)
    expected = exp_meta["runs_total"] or 0

    if count == 0:
        results.append(fail(f"runs: 0 rows — experiment saved nothing"))
        return results

    # Check energy columns not NULL
    null_energy = conn.execute(
        "SELECT COUNT(*) FROM runs WHERE exp_id = ? AND dynamic_energy_uj IS NULL",
        (exp_id,)
    ).fetchone()[0]

    if null_energy > 0:
        results.append(warn(f"runs: {count} rows, {null_energy} have NULL workload_energy_j"))
    else:
        results.append(ok(f"runs: {count} rows, all energy columns populated"))

    return results


def check_goal_execution(conn, exp_id: int, goal_ids: list) -> list:
    results = []
    count = len(goal_ids)

    if count == 0:
        results.append(fail("goal_execution: 0 rows"))
        return results

    # Check overhead_fraction not NULL
    null_overhead = conn.execute(
        "SELECT COUNT(*) FROM goal_execution WHERE exp_id = ? AND overhead_fraction IS NULL",
        (exp_id,)
    ).fetchone()[0]

    # Check success distribution
    success_count = conn.execute(
        "SELECT COUNT(*) FROM goal_execution WHERE exp_id = ? AND success = 1",
        (exp_id,)
    ).fetchone()[0]

    if null_overhead > 0:
        results.append(warn(
            f"goal_execution: {count} rows, {null_overhead} have NULL overhead_fraction"
            f" — run: python scripts/etl/goal_execution_etl.py --backfill-all"
        ))
    else:
        results.append(ok(
            f"goal_execution: {count} rows, {success_count} success, "
            f"{count-success_count} failed, overhead_fraction populated"
        ))

    return results


def check_goal_attempt(conn, goal_ids: list, attempt_ids: list) -> list:
    results = []
    count = len(attempt_ids)

    if count == 0:
        results.append(fail("goal_attempt: 0 rows"))
        return results

    if not goal_ids:
        return results

    placeholders = ",".join("?" * len(goal_ids))
    retry_count = conn.execute(
        f"SELECT COUNT(*) FROM goal_attempt WHERE goal_id IN ({placeholders}) AND is_retry = 1",
        goal_ids
    ).fetchone()[0]

    null_failure = conn.execute(
        f"SELECT COUNT(*) FROM goal_attempt WHERE goal_id IN ({placeholders}) "
        f"AND outcome = 'failure' AND failure_type IS NULL AND is_retry = 1",
        goal_ids
    ).fetchone()[0]

    msg = f"goal_attempt: {count} rows, {retry_count} retries"
    if null_failure > 0:
        msg += f", {null_failure} failures with NULL failure_type"
        results.append(warn(msg))
    else:
        results.append(ok(msg))

    return results


def check_tool_failure_events(conn, attempt_ids: list, exp_type: str) -> list:
    results = []

    if not attempt_ids:
        results.append(warn("tool_failure_events: no attempts to check"))
        return results

    placeholders = ",".join("?" * len(attempt_ids))
    count = conn.execute(
        f"SELECT COUNT(*) FROM tool_failure_events WHERE attempt_id IN ({placeholders})",
        attempt_ids
    ).fetchone()[0]

    # For failure_injection and retry_study experiments, expect > 0
    expects_failures = exp_type in ("failure_injection", "retry_study")
    if expects_failures and count == 0:
        results.append(warn(
            f"tool_failure_events: 0 rows — injection may not have fired this run "
            f"(probabilistic injection, small rep count can produce 0 failures)"
        ))
    elif count > 0:
        # Check failure types
        types = conn.execute(
            f"SELECT failure_type, COUNT(*) FROM tool_failure_events "
            f"WHERE attempt_id IN ({placeholders}) GROUP BY failure_type",
            attempt_ids
        ).fetchall()
        type_str = ", ".join(f"{t[0]}={t[1]}" for t in types)
        results.append(ok(f"tool_failure_events: {count} rows ({type_str})"))
    else:
        results.append(ok(f"tool_failure_events: 0 rows (normal for {exp_type})"))

    return results


def check_normalization_factors(conn, run_ids: list) -> list:
    results = []

    if not run_ids:
        results.append(warn("normalization_factors: no runs to check"))
        return results

    placeholders = ",".join("?" * len(run_ids))
    count = conn.execute(
        f"SELECT COUNT(*) FROM normalization_factors WHERE run_id IN ({placeholders})",
        run_ids
    ).fetchone()[0]

    missing = len(run_ids) - count
    if missing > 0:
        results.append(warn(
            f"normalization_factors: {count}/{len(run_ids)} runs have stub rows, "
            f"{missing} missing — ETL backfill will skip these"
        ))
    else:
        results.append(ok(
            f"normalization_factors: {count}/{len(run_ids)} runs have stub rows"
        ))

    return results


def check_energy_attribution(conn, run_ids: list) -> list:
    results = []

    if not run_ids:
        return results

    placeholders = ",".join("?" * len(run_ids))
    count = conn.execute(
        f"SELECT COUNT(*) FROM energy_attribution WHERE run_id IN ({placeholders})",
        run_ids
    ).fetchone()[0]

    if count == 0:
        results.append(warn("energy_attribution: 0 rows — ETL may not have run"))
    else:
        results.append(ok(f"energy_attribution: {count} rows"))

    return results

def check_run_outliers(conn, run_ids: list) -> list:
    """
    Verify run_outliers table is reachable and report current flag counts
    for the given run_ids. This is an EIC-4 registration, not a pass/fail
    gate: an unflagged run is not an error, most runs should have zero
    rows here. The check exists so a broken FK, a botched migration, or a
    detector crash shows up as a warning during integrity checks rather
    than silently producing an empty table forever.
 
    Args:
        conn: sqlite3 connection
        run_ids: run_id list for the experiment being checked
 
    Returns:
        list of ok()/warn()/fail() formatted strings, consistent with every
        other check_* function in this file.
    """
    results = []
    if not run_ids:
        return results
 
    placeholders = ",".join("?" for _ in run_ids)
 
    # DC-3 compliance: explicit try/except, not a bare except, and the
    # failure is surfaced as fail() rather than silently returning [].
    try:
        cur = conn.execute(
            f"SELECT severity, review_status, COUNT(*) "
            f"FROM run_outliers WHERE run_id IN ({placeholders}) "
            f"GROUP BY severity, review_status",
            run_ids,
        )
        rows = cur.fetchall()
    except Exception as e:
        results.append(fail(f"run_outliers: query failed ({e})"))
        return results
 
    if not rows:
        results.append(ok(f"run_outliers: 0/{len(run_ids)} runs flagged"))
        return results
 
    total_flagged = sum(count for _, _, count in rows)
    confirmed = sum(count for sev, status, count in rows if status == "confirmed")
    extreme_pending = sum(
        count for sev, status, count in rows
        if sev == "extreme" and status == "pending"
    )
 
    if extreme_pending > 0:
        # Extreme severity sitting in pending review is worth surfacing as
        # a warning at integrity check time, not just buried in a web page
        # review queue nobody is required to look at before handoff.
        results.append(warn(
            f"run_outliers: {extreme_pending} extreme-severity runs awaiting "
            f"human review (EIC-3: must be documented before chunk handoff)"
        ))
    else:
        results.append(ok(
            f"run_outliers: {total_flagged} flagged "
            f"({confirmed} confirmed excluded) across {len(run_ids)} runs"
        ))
 
    return results
def check_run_quality(conn, run_ids: list) -> list:
    results = []

    if not run_ids:
        return results

    placeholders = ",".join("?" * len(run_ids))
    count = conn.execute(
        f"SELECT COUNT(*) FROM run_quality WHERE run_id IN ({placeholders})",
        run_ids
    ).fetchone()[0]

    missing = len(run_ids) - count
    if missing > 0:
        results.append(warn(f"run_quality: {count}/{len(run_ids)} runs scored"))
    else:
        results.append(ok(f"run_quality: {count}/{len(run_ids)} runs scored"))

    return results


def check_orchestration_events(conn, run_ids: list, workflow_type: str) -> list:
    results = []

    if not run_ids:
        return results

    placeholders = ",".join("?" * len(run_ids))
    count = conn.execute(
        f"SELECT COUNT(*) FROM orchestration_events WHERE run_id IN ({placeholders})",
        run_ids
    ).fetchone()[0]

    # Expect orchestration events for agentic runs
    if workflow_type in ("agentic", "comparison") and count == 0:
        results.append(warn("orchestration_events: 0 rows for agentic experiment"))
    else:
        results.append(ok(f"orchestration_events: {count} rows"))

    return results


def check_llm_interactions(conn, run_ids: list) -> list:
    results = []

    if not run_ids:
        return results

    placeholders = ",".join("?" * len(run_ids))
    count = conn.execute(
        f"SELECT COUNT(*) FROM llm_interactions WHERE run_id IN ({placeholders})",
        run_ids
    ).fetchone()[0]

    if count == 0:
        results.append(warn("llm_interactions: 0 rows"))
    else:
        results.append(ok(f"llm_interactions: {count} rows"))

    return results


def check_recording(conn, run_ids: list) -> list:
    """Continuous recording (EIC-4): recordings covering these runs are closed or
    aborted, store no between-runs sample inside a run, and have no failed check."""
    if not run_ids or not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                       "AND name='te_thermal_samples'").fetchone():
        return []
    ph = ",".join("?" * len(run_ids))
    sessions = conn.execute(
        f"SELECT DISTINCT s.session_id, s.status FROM te_session s JOIN runs r "
        f"ON r.start_time_ns >= s.start_ns AND ((s.end_ns IS NULL AND s.status = 'open') "
        f"OR r.start_time_ns < s.end_ns) "
        f"WHERE s.run_id IS NULL AND r.run_id IN ({ph})", run_ids).fetchall()
    results = []
    for sid, status in sessions:
        if status == "open":
            results.append(warn(f"recording session={sid}: still open"))
        inside = conn.execute(
            f"SELECT COUNT(*) FROM te_thermal_samples t JOIN runs r ON t.timestamp_ns >= "
            f"r.start_time_ns AND t.timestamp_ns < r.end_time_ns WHERE t.session_id = ? "
            f"AND r.run_id IN ({ph})", [sid] + list(run_ids)).fetchone()[0]
        if inside:
            results.append(fail(f"recording session={sid}: {inside} samples inside runs"))
        failed = conn.execute("SELECT GROUP_CONCAT(check_name) FROM te_session_check "
                              "WHERE session_id = ? AND result = 'fail'", (sid,)).fetchone()[0]
        if failed:
            results.append(warn(f"recording session={sid}: failed checks {failed}"))
        results.extend(_recording_span_problems(conn, sid))
        results.extend(_recording_idle_problems(conn, sid, run_ids))
    return results


def _recording_span_problems(conn, sid) -> list:
    """Spans of one recording (EIC-4): one session span, top level and each
    workload tiled with no gap or overlap, every span with a placement."""
    try:
        from extensions.thermal_exposure.session_spans import partition_problems
    except ImportError as exc:
        return [warn(f"recording session={sid}: span check unavailable ({exc})")]
    cur = conn.execute(
        "SELECT s.span_id, s.parent_span_id, s.kind, s.start_ns, s.end_ns, s.status, "
        "p.span_id AS placed FROM spans s JOIN te_session t ON s.trace_id = t.global_session_id "
        "LEFT JOIN span_placements p ON p.span_id = s.span_id "
        "WHERE t.session_id = ? AND s.vocabulary = 'thermal_session'", (sid,))
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    if not rows:
        # recordings made before spans existed have none; not an error
        return [warn(f"recording session={sid}: no spans")]
    out = [fail(f"recording session={sid}: {p}") for p in partition_problems(rows)[:5]]
    unplaced = sum(1 for r in rows if r["placed"] is None)
    if unplaced:
        out.append(fail(f"recording session={sid}: {unplaced} spans without placement"))
    return out


def _recording_idle_problems(conn, sid, run_ids: list) -> list:
    """Idle reference of one recording (EIC-4): no energy counter row inside a
    run, and a measured reference has pre idle rows."""
    has = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                      "AND name IN ('te_energy_samples', 'te_idle_window')")}
    if "te_energy_samples" not in has:
        return []                      # store without the idle reference migration
    out = []
    ph = ",".join("?" * len(run_ids))
    inside = conn.execute(
        f"SELECT COUNT(*) FROM te_energy_samples e JOIN runs r ON e.timestamp_ns >= "
        f"r.start_time_ns AND e.timestamp_ns < r.end_time_ns WHERE e.session_id = ? "
        f"AND r.run_id IN ({ph})", [sid] + list(run_ids)).fetchone()[0]
    if inside:
        out.append(fail(f"recording session={sid}: {inside} energy counter rows inside runs"))
    ref = conn.execute("SELECT idle_reference FROM te_session WHERE session_id = ?",
                       (sid,)).fetchone()
    pre = conn.execute("SELECT COUNT(*) FROM te_idle_window WHERE session_id = ? "
                       "AND phase = 'pre'", (sid,)).fetchone()[0]
    if ref and ref[0] == "matched" and not pre:
        out.append(fail(f"recording session={sid}: measured idle reference without pre rows"))
    if ref and ref[0] is None:
        out.append(warn(f"recording session={sid}: no idle reference (recorded before it existed)"))
    return out


def check_safety_guard(conn, run_ids: list) -> list:
    """Safety guard (EIC-4): guard episodes covering these runs are closed, record a
    gate opening before each run, keep event order and host log flags, and never
    store a fresh reading without a value."""
    # Stores older than the guard migration have no guard tables: nothing to check.
    if not run_ids or not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                       "AND name='safety_guard_episodes'").fetchone():
        return []
    ph = ",".join("?" * len(run_ids))
    # Episodes are found by time: admission happens before a run row exists (EEI-4).
    episodes = conn.execute(
        f"SELECT DISTINCT e.episode_id, e.status, e.latch_reason FROM safety_guard_episodes e "
        f"JOIN runs r ON r.start_time_ns >= e.started_utc_ns AND (e.ended_utc_ns IS NULL "
        f"OR r.start_time_ns < e.ended_utc_ns) WHERE r.run_id IN ({ph})", run_ids).fetchall()
    # A run with no covering episode ran without the guard (disabled, or code older than the guard).
    if not episodes:
        return [warn("safety guard: no guard episode covers these runs")]
    results = []
    for eid, status, latch_reason in episodes:
        results.extend(_safety_guard_episode_problems(conn, eid, status, latch_reason, run_ids))
    return results


def _safety_guard_episode_problems(conn, eid, status, latch_reason, run_ids) -> list:
    """Problems of one guard episode: open or latched state, order, log flags, admissions, samples."""
    out = []
    # "running" after the process ended means close() never ran (kill or crash).
    if status == "running":
        out.append(warn(f"safety guard episode={eid}: still open (process ended without close)"))
    # A latch is a safety stop, not a data error: reported so the reader sees why runs ended.
    if status == "latched":
        out.append(warn(f"safety guard episode={eid}: latched ({latch_reason})"))
    n, lo, hi = conn.execute("SELECT COUNT(*), MIN(seq), MAX(seq) FROM safety_guard_events "
                             "WHERE episode_id = ?", (eid,)).fetchone()
    # seq is assigned under the guard lock; a gap means an event row was lost between log and store.
    if n and (lo != 1 or hi != n):
        out.append(fail(f"safety guard episode={eid}: event order has gaps ({n} events, seq {lo} to {hi})"))
    unlogged = conn.execute("SELECT COUNT(*) FROM safety_guard_events WHERE episode_id = ? "
                            "AND log_written = 0", (eid,)).fetchone()[0]
    # The host log is the crash-safe copy; a missing line weakens the incident record.
    if unlogged:
        out.append(warn(f"safety guard episode={eid}: {unlogged} events missing from the host log"))
    out.extend(_safety_guard_admission_problems(conn, eid, run_ids))
    out.extend(_safety_guard_sample_problems(conn, eid))
    return out


def _safety_guard_admission_problems(conn, eid, run_ids) -> list:
    """Every run inside the episode must follow a gate opening of that episode."""
    ph = ",".join("?" * len(run_ids))
    # Only runs that started after the episode began must have passed its gate.
    missing = conn.execute(
        f"SELECT COUNT(*) FROM runs r WHERE r.run_id IN ({ph}) AND r.start_time_ns >= "
        f"(SELECT started_utc_ns FROM safety_guard_episodes WHERE episode_id = ?) AND NOT EXISTS "
        f"(SELECT 1 FROM safety_guard_events g WHERE g.episode_id = ? AND g.event_type = 'gate_open' "
        f"AND g.utc_ns <= r.start_time_ns)", list(run_ids) + [eid, eid]).fetchone()[0]
    # A run without a preceding gate opening means some path bypassed the guard.
    if missing:
        return [fail(f"safety guard episode={eid}: {missing} runs without a gate opening before them")]
    return []


def _safety_guard_sample_problems(conn, eid) -> list:
    """Fresh readings must carry a value (MIC-1); all-zero headroom over 10+ samples is suspect (MIC-3)."""
    out = []
    bad = conn.execute("SELECT COUNT(*) FROM safety_guard_samples WHERE episode_id = ? AND "
                       "min_quality = 'fresh' AND min_headroom_c IS NULL", (eid,)).fetchone()[0]
    # MIC-1: a fresh reading always has a value; NULL with "fresh" would hide a sensor gap.
    if bad:
        out.append(fail(f"safety guard episode={eid}: {bad} fresh samples without a value"))
    n, zeros = conn.execute("SELECT COUNT(min_headroom_c), SUM(min_headroom_c = 0) FROM "
                            "safety_guard_samples WHERE episode_id = ?", (eid,)).fetchone()
    # MIC-3 sentinel: a real margin is never exactly 0 for ten readings in a row.
    if n >= 10 and zeros == n:
        out.append(warn(f"safety guard episode={eid}: headroom is 0 in all {n} samples (sensor fault?)"))
    # No value at all: the guard was blind for the whole episode.
    if n == 0:
        out.append(warn(f"safety guard episode={eid}: no sample with a headroom value"))
    return out


def check_dcgm_coverage(conn, run_ids: list) -> list:
    """GPU sampling coverage per run (EIC-4, MIC-3): one row per run with DCGM
    samples, no cap, run window coverage at least 0.99, gaps reported."""
    results = []
    if not run_ids:
        return results
    table = "energy_validation_dcgm_coverage"
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (table,)).fetchone():
        return [warn("dcgm_coverage: table absent (energy_validation inactive)")]
    ph = ",".join("?" * len(run_ids))
    # Only runs made after the coverage record existed (e001 applied) must have a row
    applied = conn.execute("SELECT MIN(applied_at) FROM migration_history "
                           "WHERE source = 'ext:energy_validation' AND status = 'applied'").fetchone()[0]
    since_ns = (conn.execute("SELECT CAST(strftime('%s', ?) AS INTEGER) * 1000000000",
                             (applied,)).fetchone()[0] if applied else None) or 0
    n_dcgm = conn.execute(f"SELECT COUNT(DISTINCT g.run_id) FROM gpu_samples g JOIN runs r "
                          f"USING(run_id) WHERE g.source='dcgm' AND g.run_id IN ({ph}) "
                          f"AND r.start_time_ns >= ?", run_ids + [since_ns]).fetchone()[0]
    n_old = conn.execute(f"SELECT COUNT(DISTINCT g.run_id) FROM gpu_samples g JOIN runs r "
                         f"USING(run_id) WHERE g.source='dcgm' AND g.run_id IN ({ph}) "
                         f"AND r.start_time_ns < ?", run_ids + [since_ns]).fetchone()[0]
    if n_old:
        results.append(warn(f"dcgm_coverage: {n_old} runs predate the coverage record (no row expected)"))
    rows = conn.execute(f"SELECT run_id, shared_window_coverage, coverage_null_reason, "
                        f"cap_detected, interior_gaps_gt_300ms FROM {table} "
                        f"WHERE run_id IN ({ph})", run_ids).fetchall()
    if len(rows) < n_dcgm:
        results.append(fail(f"dcgm_coverage: {len(rows)} rows for {n_dcgm} runs with DCGM samples"))
    for run_id, share, reason, cap, gaps in rows:
        if cap:
            results.append(fail(f"dcgm_coverage run={run_id}: sample cap detected"))
        elif share is None:
            results.append(warn(f"dcgm_coverage run={run_id}: NULL ({reason})"))
        elif share < 0.99:
            results.append(warn(f"dcgm_coverage run={run_id}: {share:.4f} < 0.99"))
        if gaps:
            results.append(warn(f"dcgm_coverage run={run_id}: {gaps} interior gaps > 300 ms"))
    if rows and all(r[1] == 0 for r in rows):
        results.append(fail("dcgm_coverage: all coverage values zero (sentinel)"))
    if not results:
        results.append(ok(f"dcgm_coverage: {len(rows)} runs, all >= 0.99, no cap, no gaps"))
    return results


def _table_exists(conn, table: str) -> bool:
    """True when a namespace table exists (its extension migration applied)."""
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                             (table,)).fetchone())


def check_engine_provenance(conn, exp_id: int) -> list:
    """Serving instance per experiment (EIC-4): rows only for local providers;
    pid_start_time with confirmed workers is the full match."""
    table = "energy_validation_engine_provenance"
    if not _table_exists(conn, table):
        return [warn("engine_provenance: table absent (energy_validation inactive)")]
    rows = conn.execute(f"SELECT match_method, worker_association_status, engine, engine_version "
                        f"FROM {table} WHERE exp_id = ?", (exp_id,)).fetchall()
    if not rows:
        return [ok("engine_provenance: no row (remote or cloud provider)")]
    results = []
    for method, status, engine, version in rows:
        if method != "pid_start_time":
            results.append(warn(f"engine_provenance: {method} (server not started by a serve script)"))
        elif status != "confirmed":
            results.append(warn(f"engine_provenance: {engine} {version} workers {status}"))
    if not results:
        results.append(ok(f"engine_provenance: {len(rows)} instance(s), matched, workers confirmed"))
    return results


def check_server_age(conn, run_ids: list) -> list:
    """Age at run start per run (EIC-4, MIC-3): one row per run, boot age filled."""
    table = "energy_validation_server_age"
    if not run_ids:
        return []
    if not _table_exists(conn, table):
        return [warn("server_age: table absent (energy_validation inactive)")]
    ph = ",".join("?" * len(run_ids))
    rows = conn.execute(f"SELECT run_id, time_since_boot_s, time_since_server_start_s "
                        f"FROM {table} WHERE run_id IN ({ph})", run_ids).fetchall()
    results = []
    if len(rows) < len(run_ids):
        results.append(warn(f"server_age: {len(rows)} rows for {len(run_ids)} runs "
                            f"(runs before e003 have none)"))
    if rows and all(r[1] is None for r in rows):
        results.append(fail("server_age: time_since_boot_s NULL on every run"))
    if rows and all(r[2] is None for r in rows):
        results.append(warn("server_age: no server start time (no matched serving instance)"))
    if not results:
        results.append(ok(f"server_age: {len(rows)} runs, boot and server age filled"))
    return results


def check_serving_snapshots(conn, run_ids: list) -> list:
    """KV and queue snapshots during runs (EIC-4, MIC-3): both types present,
    KV occupancy not zero on every sample (sentinel)."""
    if not run_ids:
        return []
    ph = ",".join("?" * len(run_ids))
    rows = conn.execute(f"SELECT snapshot_type, COUNT(*), SUM(kv_occupancy_fraction > 0), "
                        f"SUM(kv_occupancy_fraction IS NULL), SUM(queue_active > 0), "
                        f"SUM(queue_active IS NOT NULL) FROM serving_runtime_snapshots "
                        f"WHERE run_id IN ({ph}) GROUP BY snapshot_type", run_ids).fetchall()
    kinds = {r[0]: r for r in rows}
    if not kinds:
        return [warn("serving_snapshots: none (no local serving engine answered)")]
    results = []
    if "queue" not in kinds or "kv_cache" not in kinds:
        results.append(fail(f"serving_snapshots: types present {sorted(kinds)}; need kv_cache and queue"))
    kv, queue = kinds.get("kv_cache"), kinds.get("queue")
    if kv and kv[1] and not kv[2] and kv[3] < kv[1]:
        # zero is the true value on an idle server; it is a sentinel only while requests ran
        if not queue or not queue[5]:
            results.append(warn("serving_snapshots: kv_occupancy_fraction zero on every sample, "
                                "request activity unknown"))
        elif queue[4]:
            results.append(fail("serving_snapshots: kv_occupancy_fraction zero on every sample "
                                "while requests were active (sentinel)"))
        else:
            results.append(ok("serving_snapshots: idle server (no active requests), kv occupancy 0, "
                              + ", ".join(f"{k} {v[1]}" for k, v in kinds.items())))
    if not results:
        results.append(ok("serving_snapshots: " + ", ".join(f"{k} {v[1]}" for k, v in kinds.items())))
    return results


def check_llm_ttft(conn, run_ids: list) -> list:
    """ttft honesty (v126): no ttft without a streamed first token; a NULL
    ttft carries its reason."""
    if not run_ids:
        return []
    ph = ",".join("?" * len(run_ids))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(llm_interactions)")]
    if "ttft_null_reason" not in cols:
        return [warn("llm_ttft: ttft_null_reason absent (schema before v126)")]
    fabricated = conn.execute(f"SELECT COUNT(*) FROM llm_interactions WHERE run_id IN ({ph}) "
                              f"AND streaming_enabled = 0 AND ttft_ms IS NOT NULL", run_ids).fetchone()[0]
    unexplained = conn.execute(f"SELECT COUNT(*) FROM llm_interactions WHERE run_id IN ({ph}) "
                               f"AND ttft_ms IS NULL AND ttft_null_reason IS NULL", run_ids).fetchone()[0]
    results = []
    if fabricated:
        results.append(fail(f"llm_ttft: {fabricated} calls with ttft but no stream (fabricated)"))
    if unexplained:
        results.append(warn(f"llm_ttft: {unexplained} NULL ttft without reason (rows before v126 or adapter without reason)"))
    if not results:
        results.append(ok("llm_ttft: every ttft measured or NULL with a reason"))
    return results


def check_gpu_mem_traffic(conn, run_ids: list) -> list:
    """Traffic reader record per run (EIC-4, MIC-3): status with reason; measured
    runs have samples; byte columns not zero on every sample (sentinel)."""
    runs_t, samp_t = "energy_validation_gpu_mem_traffic_runs", "energy_validation_gpu_mem_traffic_samples"
    if not run_ids:
        return []
    if not _table_exists(conn, runs_t):
        return [warn("gpu_mem_traffic: tables absent (energy_validation inactive)")]
    ph = ",".join("?" * len(run_ids))
    rows = conn.execute(f"SELECT run_id, reader, status, reason, sample_count FROM {runs_t} "
                        f"WHERE run_id IN ({ph})", run_ids).fetchall()
    if not rows:
        return [warn("gpu_mem_traffic: no reader record (no traffic plugin installed, or runs before e004)")]
    results = []
    for run_id, reader, status, reason, n in rows:
        if status == "measured" and not n:
            results.append(fail(f"gpu_mem_traffic run={run_id}: measured with 0 samples"))
        elif status != "measured":
            results.append(warn(f"gpu_mem_traffic run={run_id} {reader}: {status} ({reason})"))
    nz = conn.execute(f"SELECT COUNT(*), SUM(all_read_miss_bytes > 0 OR all_write_bytes > 0) "
                      f"FROM {samp_t} WHERE run_id IN ({ph})", run_ids).fetchone()
    if nz[0] and not nz[1]:
        results.append(fail("gpu_mem_traffic: all byte columns zero on every sample (sentinel)"))
    if not results:
        results.append(ok(f"gpu_mem_traffic: {len(rows)} runs measured, {nz[0]} samples"))
    return results
    """Tables expected empty — verify they haven't been accidentally populated."""
    results = []

    # hallucination_events and output_quality owned by 8.5-C
    for table in ("hallucination_events", "output_quality", "output_quality_judges"):
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        results.append(ok(f"{table}: {count} rows (8.5-C owns this)"))

    return results
def check_spans(conn, exp_id: int, run_ids: list) -> list:
    """
    45.8 span checks for one experiment (spans written by the 45.8 code; rows with
    timing_quality NULL are older build time spans and are only counted).

      span_times   run span = runs window; phase and tool spans = their orchestration event;
                   llm spans start at a request_start_ns of the run; each endpoint compared
                   separately (tolerance ALEMS_SPAN_TOL_MS, default 1 ms)
      span_nesting every child inside its parent where both are known; one root per trace
      span_links   goal_execution.span_id = a root goal span; goal_attempt.span_id = an attempt
                   span with the same attempt_number whose run child has the attempt's run;
                   orchestration_events.span_id = a span of the same run and kind
    """
    import os
    if not _table_exists(conn, "spans") or not run_ids:
        return [warn("spans: no spans table or no runs")]
    tol = int(float(os.environ.get("ALEMS_SPAN_TOL_MS") or 1.0) * 1e6)
    qm = ",".join("?" * len(run_ids))
    legacy = conn.execute(f"SELECT COUNT(*) FROM spans WHERE run_id IN ({qm}) AND timing_quality IS NULL",
                          run_ids).fetchone()[0]
    spans = {r[0]: r for r in conn.execute(
        f"SELECT span_id, trace_id, parent_span_id, run_id, kind, start_ns, end_ns, timing_quality "
        f"FROM spans WHERE run_id IN ({qm}) AND timing_quality IS NOT NULL", run_ids)}
    if not spans:
        return [warn(f"spans: no 45.8 spans for these runs ({legacy} older build time spans)")]
    results = []
    bad = []

    # span_times
    for sid, tr, par, rid, kind, s0, s1, q in spans.values():
        if kind == "run":
            w = conn.execute("SELECT start_time_ns, end_time_ns FROM runs WHERE run_id = ?", (rid,)).fetchone()
            if w and (abs(s0 - w[0]) > tol or (s1 is not None and w[1] is not None and abs(s1 - w[1]) > tol)):
                bad.append(f"run span {sid[:8]} vs runs window: start {(s0 - w[0]) / 1e6:.3f} ms, "
                           f"end {((s1 or 0) - (w[1] or 0)) / 1e6:.3f} ms")
    for sid, rid, e0, e1 in conn.execute(
            f"SELECT span_id, run_id, start_time_ns, end_time_ns FROM orchestration_events "
            f"WHERE run_id IN ({qm}) AND span_id IS NOT NULL", run_ids):
        sp = spans.get(sid)
        if sp is None:
            continue
        if abs(sp[5] - e0) > tol or (sp[6] is not None and e1 is not None and abs(sp[6] - e1) > tol):
            bad.append(f"event span {sid[:8]}: start {(sp[5] - e0) / 1e6:.3f} ms, end "
                       f"{((sp[6] or 0) - (e1 or 0)) / 1e6:.3f} ms from its event")
    for rid in run_ids:
        starts = {r[0] for r in conn.execute("SELECT request_start_ns FROM llm_interactions WHERE run_id = ? "
                                             "AND request_start_ns IS NOT NULL", (rid,))}
        for sp in (s for s in spans.values() if s[3] == rid and s[4] == "llm_call"):
            if not any(abs(sp[5] - x) <= tol for x in starts):
                bad.append(f"llm span {sp[0][:8]} start matches no request of run {rid}")
    results.append(fail("span_times: " + "; ".join(bad[:5])) if bad else
                   ok(f"span_times: {len(spans)} spans agree with their sources"))

    # span_nesting
    bad = []
    for sid, tr, par, rid, kind, s0, s1, q in spans.values():
        p = spans.get(par) if par else None
        if p is None:
            continue
        if s0 < p[5] - tol or (s1 is not None and p[6] is not None and s1 > p[6] + tol):
            bad.append(f"{kind} {sid[:8]} outside its {p[4]}")
    traces = {}
    for s in spans.values():
        traces.setdefault(s[1], []).append(s)
    for tr, members in traces.items():
        roots = conn.execute("SELECT COUNT(*) FROM spans WHERE trace_id = ? AND parent_span_id IS NULL",
                             (tr,)).fetchone()[0]
        if roots != 1:
            bad.append(f"trace {tr[:8]} has {roots} roots")
    results.append(fail("span_nesting: " + "; ".join(bad[:5])) if bad else
                   ok(f"span_nesting: {len(traces)} traces, one root each, children inside parents"))

    # span_links
    bad = []
    for gid, gsid in conn.execute("SELECT goal_id, span_id FROM goal_execution WHERE exp_id = ?", (exp_id,)):
        g = conn.execute("SELECT kind, parent_span_id FROM spans WHERE span_id = ?", (gsid,)).fetchone() if gsid else None
        if g is None or g[0] != "goal" or g[1] is not None:
            bad.append(f"goal {gid}: span_id {gsid} is not a root goal span")
        for aid, num, arid, asid in conn.execute("SELECT attempt_id, attempt_number, run_id, span_id FROM goal_attempt "
                                                 "WHERE goal_id = ?", (gid,)):
            a = conn.execute("SELECT kind, parent_span_id FROM spans WHERE span_id = ?", (asid,)).fetchone() if asid else None
            if a is None or a[0] != "attempt" or (gsid and a[1] != gsid):
                bad.append(f"attempt {aid}: span_id {asid} is not an attempt span of goal {gid}")
                continue
            n = conn.execute("SELECT value_text FROM span_attributes WHERE span_id = ? AND key = 'attempt_number'",
                             (asid,)).fetchone()
            if n is None or int(n[0]) != num:
                bad.append(f"attempt {aid}: span attempt_number {n[0] if n else None} != {num}")
            if arid and not conn.execute("SELECT 1 FROM spans WHERE parent_span_id = ? AND kind = 'run' AND run_id = ?",
                                         (asid, arid)).fetchone():
                bad.append(f"attempt {aid}: no run span of run {arid} under its attempt span")
    for eid, rid, etype, esid in conn.execute(
            f"SELECT event_id, run_id, event_type, span_id FROM orchestration_events WHERE run_id IN ({qm}) "
            f"AND span_id IS NOT NULL", run_ids):
        sp = conn.execute("SELECT run_id, kind FROM spans WHERE span_id = ?", (esid,)).fetchone()
        want = "tool_call" if etype == "tool_call" else "phase"
        if sp is None or sp[0] != rid or sp[1] != want:
            bad.append(f"event {eid}: span {esid} is {sp} not a {want} of run {rid}")
    results.append(fail("span_links: " + "; ".join(bad[:5])) if bad else
                   ok("span_links: goal, attempt and event links resolve to the right spans"))
    if legacy:
        results.append(warn(f"spans: {legacy} older build time spans not checked (45.8 repair)"))
    return results


def check_expected_empty(conn) -> list:
    """Tables expected empty — verify they haven't been accidentally populated."""
    results = []

    # hallucination_events and output_quality owned by 8.5-C
    for table in ("hallucination_events", "output_quality", "output_quality_judges"):
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        results.append(ok(f"{table}: {count} rows (8.5-C owns this)"))

    return results


def check_etl_queue(conn, goal_ids: list) -> list:
    """etl_queue is deprecated (chunk 45, F6): no writer, no reader. Historical rows kept."""
    return [ok("etl_queue: deprecated, not written since chunk 45 (historical rows kept)")]


def _check_etl_queue_legacy(conn, goal_ids: list) -> list:
    """Previous check, kept for reading old stores by hand."""
    results = []
    if not goal_ids:
        results.append(warn("etl_queue: no goals to check"))
        return results
    count = conn.execute("SELECT COUNT(*) FROM etl_queue WHERE entity_id IN ({})".format(
        ",".join("?" * len(goal_ids))), goal_ids).fetchone()[0]
    if count == 0:
        results.append(fail("etl_queue: no entries for experiment goals"))
        return results
    unprocessed = conn.execute(
        "SELECT COUNT(*) FROM etl_queue WHERE entity_id IN ({}) AND status != 'done'".format(
        ",".join("?" * len(goal_ids))), goal_ids).fetchone()[0]
    if unprocessed > 0:
        results.append(warn(f"etl_queue: {unprocessed} entries not done (status != done)"))
    else:
        results.append(ok(f"etl_queue: {count} entries, all done"))
    return results


RUN_WINDOW_TOL_UJ = 1000            # counter resolution is 1 mJ
RUN_WINDOW_W_RANGE = (1.0, 400.0)   # plausible package power on any fleet machine, watts


def check_run_window(conn, run_ids: list) -> list:
    """Task window [t0, t1] checks (window contract), for measurement_boundary_v2 runs:
    pre + task + post equals the counter span, and each segment has plausible power."""
    results = []
    if not run_ids:
        return results
    ph = ",".join("?" * len(run_ids))
    v2 = {r for (r,) in conn.execute(
        f"SELECT DISTINCT run_id FROM measurement_methodology WHERE run_id IN ({ph}) "
        f"AND metric_id = 'task_duration_ns' AND method_id = 'measurement_boundary_v2'",
        run_ids)}
    legacy = len(set(run_ids) - v2)
    if legacy:
        results.append(warn(f"run_window: {legacy} runs predate measurement_boundary_v2 (not checked)"))
    rows = conn.execute(
        f"SELECT run_id, pre_task_energy_uj, pkg_energy_uj, post_task_energy_uj, "
        f"rapl_before_pretask_uj, rapl_after_task_uj, pre_task_duration_ns, "
        f"task_duration_ns, post_task_duration_ns FROM runs WHERE run_id IN ({ph})",
        run_ids).fetchall()
    n_ok = 0
    for rid, pre, task, post, before, after, d_pre, d_task, d_post in rows:
        if rid not in v2:
            continue
        if None in (pre, task, post, before, after):
            results.append(warn(f"run_window run={rid}: a boundary term is NULL"))
            continue
        gap = pre + task + post - (after - before)
        if abs(gap) > RUN_WINDOW_TOL_UJ:
            results.append(fail(f"run_window run={rid}: pre+task+post off by {gap} uJ"))
            continue
        bad = False
        for name, e, d in (("pre", pre, d_pre), ("task", task, d_task), ("post", post, d_post)):
            if not d:
                continue
            w = e / d * 1e3
            if not RUN_WINDOW_W_RANGE[0] <= w <= RUN_WINDOW_W_RANGE[1]:
                results.append(fail(f"run_window run={rid}: {name} power {w:.1f} W implausible"))
                bad = True
        n_ok += 0 if bad else 1
    if v2 and n_ok == len(v2):
        results.append(ok(f"run_window: {n_ok} runs conserve within 1 mJ, powers plausible"))
    return results


# Policy names seeded by migrations/seed/s003_retry_policy.sql (source of truth).
# Extra rows (user defined policies) are allowed; a missing seeded name fails.
SEEDED_RETRY_POLICIES = ("no_retry", "default", "aggressive")


def check_retry_policy(conn) -> list:
    """Verify every policy seeded by s003 is present, matched by name."""
    results = []
    names = {r[0] for r in conn.execute("SELECT policy_name FROM retry_policy")}
    missing = sorted(set(SEEDED_RETRY_POLICIES) - names)
    if missing:
        results.append(fail(f"retry_policy: seeded policies missing: {missing}"))
    else:
        results.append(ok(f"retry_policy: {len(SEEDED_RETRY_POLICIES)} seeded policies present"))
    return results


def check_task_categories(conn) -> list:
    """Verify task_categories populated. Warn if < 26 (8.5-C.pre not yet run)."""
    results = []
    count = conn.execute("SELECT COUNT(*) FROM task_categories").fetchone()[0]
    if count == 0:
        results.append(fail("task_categories: 0 rows — seed not run"))
    elif count < 16:
        results.append(fail(f"task_categories: only {count} rows — expected >= 16"))
    elif count < 26:
        results.append(warn(f"task_categories: {count} rows — 8.5-C.pre migration 034 not yet run"))
    else:
        results.append(ok(f"task_categories: {count} rows"))
    return results


def check_task_retry_override(conn) -> list:
    """Verify task_retry_override rows are valid. 0 rows is acceptable."""
    results = []
    count = conn.execute("SELECT COUNT(*) FROM task_retry_override").fetchone()[0]
    if count == 0:
        results.append(ok("task_retry_override: 0 rows (no overrides configured — acceptable)"))
        return results
    orphans = conn.execute("""
        SELECT COUNT(*) FROM task_retry_override t
        WHERE NOT EXISTS (SELECT 1 FROM retry_policy p WHERE p.policy_name = t.policy_name)
    """).fetchone()[0]
    if orphans > 0:
        results.append(fail(f"task_retry_override: {orphans} orphan policy_name references"))
    else:
        results.append(ok(f"task_retry_override: {count} overrides, all valid"))
    return results

def _conservation_own_runs(conn, exp_id: int) -> list:
    """Attempts that own their run: attempt energy must equal the run's attributed energy."""
    return conn.execute("""
        SELECT ga.goal_id, ga.attempt_id, ga.run_id,
            ABS(ga.energy_uj - r.attributed_energy_uj) AS delta
        FROM goal_attempt ga
        JOIN goal_execution ge ON ge.goal_id = ga.goal_id
        JOIN runs r ON r.run_id = ga.run_id
        WHERE ge.exp_id = ? AND ga.energy_uj IS NOT NULL
          AND r.attributed_energy_uj IS NOT NULL
          AND (SELECT COUNT(*) FROM goal_attempt x WHERE x.run_id = ga.run_id) = 1
    """, (exp_id,)).fetchall()


def _conservation_shared_runs(conn, exp_id: int) -> list:
    """Old era goals whose winning run is shared by several attempts: run energy = attempt sum."""
    return conn.execute("""
        SELECT ge.goal_id,
            ABS(r.attributed_energy_uj - SUM(ga.energy_uj)) AS delta
        FROM goal_execution ge
        JOIN runs r ON r.run_id = ge.winning_run_id
        JOIN goal_attempt ga ON ga.goal_id = ge.goal_id
        WHERE ge.exp_id = ? AND ga.energy_uj IS NOT NULL
          AND (SELECT COUNT(*) FROM goal_attempt x WHERE x.run_id = ge.winning_run_id) > 1
        GROUP BY ge.goal_id
    """, (exp_id,)).fetchall()


def check_energy_conservation(conn, exp_id: int, run_ids: list, goal_ids: list) -> list:
    results = []
    own = _conservation_own_runs(conn, exp_id)
    shared = _conservation_shared_runs(conn, exp_id)
    if not own and not shared:
        results.append(warn("energy_conservation: no attempts with measured energy to verify"))
    bad_own = [r for r in own if r["delta"] > 1000]
    for v in bad_own:
        results.append(fail(f"energy_conservation VIOLATION goal={v['goal_id']} attempt={v['attempt_id']} run={v['run_id']} delta={v['delta']}µJ"))
    if own and not bad_own:
        results.append(ok(f"energy_conservation: {len(own)} attempts equal their own run, max_delta={max(r['delta'] for r in own)}µJ"))
    bad_shared = [r for r in shared if r["delta"] > 1000]
    for v in bad_shared:
        results.append(fail(f"energy_conservation VIOLATION (shared_legacy) goal={v['goal_id']} delta={v['delta']}µJ"))
    if shared and not bad_shared:
        results.append(ok(f"energy_conservation: {len(shared)} shared_legacy goals equal attempt sum"))
    rows2 = conn.execute("""
        SELECT ge.goal_id, ge.total_energy_uj, SUM(ga.energy_uj) AS attempt_sum
        FROM goal_execution ge
        JOIN goal_attempt ga ON ga.goal_id = ge.goal_id
        WHERE ge.exp_id = ? AND ga.energy_uj IS NOT NULL
        GROUP BY ge.goal_id HAVING ge.total_energy_uj IS NOT NULL
    """, (exp_id,)).fetchall()
    violations2 = [r for r in rows2 if abs((r["total_energy_uj"] or 0) - (r["attempt_sum"] or 0)) > 1000]
    if violations2:
        for v in violations2:
            results.append(warn(f"goal_execution.total mismatch goal={v['goal_id']} stored={v['total_energy_uj']} computed={v['attempt_sum']}"))
    elif rows2:
        results.append(ok(f"goal_execution.total matches attempt sums: {len(rows2)} goals"))

    # D1: E_attributed = E_llm_window + E_orchestration
    # llm_window = llm_compute_energy_uj (residual guaranteed by ETL construction)
    d1_rows = conn.execute("""
        SELECT ea.run_id,
            ABS(r.attributed_energy_uj
                - COALESCE(ea.llm_compute_energy_uj, 0)
                - COALESCE(ea.orchestration_energy_uj, 0)) AS d1_delta
        FROM energy_attribution ea
        JOIN runs r ON r.run_id = ea.run_id
        WHERE ea.run_id IN ({placeholders})
          AND r.attributed_energy_uj > 0
    """.format(placeholders=",".join("?" * len(run_ids))), run_ids).fetchall()
    d1_violations = [r for r in d1_rows if r["d1_delta"] > 1000]
    if d1_violations:
        for v in d1_violations:
            results.append(fail(f"D1 VIOLATION run={v['run_id']} delta={v['d1_delta']}µJ"))
    elif d1_rows:
        results.append(ok(f"D1 conservation: {len(d1_rows)} runs verified max_delta={max(r['d1_delta'] for r in d1_rows)}µJ"))
 
    # D4: E_pkg = E_core + E_uncore + E_dram, only where all four terms were measured.
    # Unmeasured domains are NULL, so the identity is not applicable to those runs.
    d4_rows = conn.execute("""
        SELECT ea.run_id,
            ABS(ea.pkg_energy_uj - ea.core_energy_uj
                - ea.uncore_energy_uj - ea.dram_energy_uj) AS d4_delta
        FROM energy_attribution ea
        WHERE ea.run_id IN ({placeholders})
          AND ea.pkg_energy_uj IS NOT NULL AND ea.core_energy_uj IS NOT NULL
          AND ea.uncore_energy_uj IS NOT NULL AND ea.dram_energy_uj IS NOT NULL
    """.format(placeholders=",".join("?" * len(run_ids))), run_ids).fetchall()
    d4_violations = [r for r in d4_rows if r["d4_delta"] > (0.01 * 1e6)]
    if d4_violations:
        for v in d4_violations:
            results.append(warn(f"D4 delta run={v['run_id']} delta={v['d4_delta']}µJ"))
    elif d4_rows:
        results.append(ok(f"D4 conservation: {len(d4_rows)} runs verified"))
    else:
        results.append(ok("D4 conservation: not applicable on this platform (a domain is unmeasured)"))
 
    # D3b: E_dynamic = E_attributed + E_background
    d3b_rows = conn.execute("""
        SELECT r.run_id,
            ABS(r.dynamic_energy_uj
                - r.attributed_energy_uj
                - COALESCE(ea.background_energy_uj, 0)) AS d3b_delta
        FROM runs r
        JOIN energy_attribution ea ON ea.run_id = r.run_id
        WHERE r.run_id IN ({placeholders})
          AND r.dynamic_energy_uj > 0
    """.format(placeholders=",".join("?" * len(run_ids))), run_ids).fetchall()
    d3b_violations = [r for r in d3b_rows if r["d3b_delta"] > 1000]
    if d3b_violations:
        for v in d3b_violations:
            results.append(fail(f"D3b VIOLATION run={v['run_id']} delta={v['d3b_delta']}µJ"))
    elif d3b_rows:
        results.append(ok(f"D3b conservation: {len(d3b_rows)} runs verified"))
 
    # D2: E_attributed = E_planning + E_execution + E_synthesis + E_inter_phase
    d2_rows = conn.execute("""
        SELECT ea.run_id,
            ABS(r.attributed_energy_uj
                - COALESCE(ea.planning_energy_uj, 0)
                - COALESCE(ea.execution_energy_uj, 0)
                - COALESCE(ea.synthesis_energy_uj, 0)
                - COALESCE(ea.inter_phase_energy_uj, 0)
                - COALESCE(ea.attribution_residual_uj, 0)) AS d2_delta,   -- v3 residual (NULL for v2)
            ea.attribution_residual_uj AS resid, r.attributed_energy_uj AS attr
        FROM energy_attribution ea
        JOIN runs r ON r.run_id = ea.run_id
        WHERE ea.run_id IN ({placeholders})
          AND r.attributed_energy_uj > 0
          AND ea.planning_energy_uj > 0
    """.format(placeholders=",".join("?" * len(run_ids))), run_ids).fetchall()
    d2_violations = [r for r in d2_rows if r["d2_delta"] > 1000]
    # v3: residual size is reported, never a fail (claude/DESIGN_45_phase_attribution.md)
    _tol = _phase_residual_warn_fraction()
    for r in d2_rows:
        if r["resid"] is not None and r["attr"] and abs(r["resid"]) / r["attr"] > _tol:
            results.append(warn(f"D2 residual run={r['run_id']}: {r['resid'] / 1e6:+.3f} J "
                                f"({abs(r['resid']) / r['attr']:.1%} of attributed > {_tol:.0%})"))
    if d2_violations:
        for v in d2_violations:
            results.append(fail(f"D2 VIOLATION run={v['run_id']} delta={v['d2_delta']}µJ"))
    elif d2_rows:
        results.append(ok(f"D2 conservation: {len(d2_rows)} runs verified"))
    else:
        results.append(warn("D2 conservation: no agentic runs with phase data to verify"))
    return results


def _phase_residual_warn_fraction() -> float:
    """attribution.phase_residual_warn_fraction from config/app_settings.yaml."""
    import yaml
    from pathlib import Path
    cfg = Path(__file__).resolve().parents[1] / "config" / "app_settings.yaml"
    return float(((yaml.safe_load(cfg.read_text()) or {}).get("attribution") or {})
                 ["phase_residual_warn_fraction"])


def _energy_consistency_bounds():
    """energy_consistency.ratio_low / ratio_high from config/app_settings.yaml."""
    import yaml
    from pathlib import Path
    cfg = Path(__file__).resolve().parents[1] / "config" / "app_settings.yaml"
    ec = (yaml.safe_load(cfg.read_text()) or {}).get("energy_consistency") or {}
    return float(ec["ratio_low"]), float(ec["ratio_high"])


def check_counter_samples(conn, run_ids: list) -> list:
    """Run counter energy vs package samples over [t0, t1], coverage corrected
    (claude/FINDING_45_prod_energy_consistency.md). Disagreement means one of the
    two energy sources is wrong; the paper must not use such a run."""
    results = []
    if not run_ids:
        return results
    lo, hi = _energy_consistency_bounds()
    dom = conn.execute("SELECT domain_id FROM energy_domains WHERE name = 'PACKAGE' LIMIT 1").fetchone()
    n_ok, unassessable = 0, []
    for rid in run_ids:
        r = conn.execute("SELECT pkg_energy_uj, start_time_ns, pre_task_duration_ns, task_duration_ns "
                         "FROM runs WHERE run_id = ?", (rid,)).fetchone()
        if not r or None in tuple(r):
            unassessable.append(rid)
            continue
        counter, t0 = r[0], r[1] + r[2]
        t1 = t0 + r[3]
        row = None
        if dom:
            row = conn.execute(
                "SELECT SUM(esd.energy_uj), SUM(esv.interval_ns) FROM energy_sample_domains esd "
                "JOIN energy_samples_v2 esv ON esv.sample_id = esd.sample_id "
                "WHERE esd.run_id = ? AND esd.domain_id = ? AND esv.timestamp_ns - esv.interval_ns >= ? "
                "AND esv.timestamp_ns <= ?", (rid, dom[0], t0, t1)).fetchone()
        if not row or not row[0]:
            row = conn.execute(
                "SELECT SUM(pkg_end_uj - pkg_start_uj), SUM(sample_end_ns - sample_start_ns) FROM energy_samples "
                "WHERE run_id = ? AND sample_start_ns >= ? AND sample_end_ns <= ? "
                "AND pkg_end_uj IS NOT NULL AND pkg_start_uj IS NOT NULL", (rid, t0, t1)).fetchone()
        samp, cov = (row[0], row[1]) if row else (None, None)
        if not samp or not cov or t1 <= t0:
            unassessable.append(rid)
            continue
        score = (counter / samp) * min(1.0, cov / (t1 - t0))
        if not lo <= score <= hi:
            results.append(fail(f"counter_samples run={rid}: counter {counter / 1e6:.1f} J vs samples "
                                f"{samp / 1e6:.1f} J over {cov / 1e9:.2f} of {(t1 - t0) / 1e9:.2f} s "
                                f"(score {score:.2f} outside [{lo}, {hi}])"))
        else:
            n_ok += 1
    if unassessable:
        results.append(warn(f"counter_samples: {len(unassessable)} runs unassessable (no window or samples)"))
    if n_ok and not any(x for x in results if "counter_samples run=" in str(x)):
        results.append(ok(f"counter_samples: {n_ok} runs, counter and samples agree"))
    return results


def _energy_consistency(key):
    """One energy_consistency setting from config/app_settings.yaml."""
    import yaml
    from pathlib import Path
    cfg = Path(__file__).resolve().parents[1] / "config" / "app_settings.yaml"
    return float(((yaml.safe_load(cfg.read_text()) or {}).get("energy_consistency") or {})[key])


def check_tick_health(conn, run_ids: list) -> list:
    """Process and machine tick counters behind every CPU share (chunk 45 F3):
    process ticks recorded and moving, total_ticks_end stored, nice present on v129 rows."""
    results = []
    if not run_ids:
        return results
    min_s = _energy_consistency("tick_min_task_s")
    has_nice = any(r[1] == "nice_ticks_start" for r in conn.execute("PRAGMA table_info(interrupt_samples)"))
    nice_sql = "SUM(nice_ticks_start IS NOT NULL)" if has_nice else "0"
    bad = 0
    for rid in run_ids:
        row = conn.execute(f"""
            SELECT COUNT(*) n, COUNT(proc_ticks_start) n_proc,
                   SUM(proc_ticks_end - proc_ticks_start) d_proc,
                   SUM(total_ticks_end <> total_ticks_start) n_total_moves, {nice_sql} n_nice,
                   (SELECT task_duration_ns FROM runs WHERE run_id = ?) task_ns
            FROM interrupt_samples WHERE run_id = ?""", (rid, rid)).fetchone()
        n, n_proc, d_proc, n_tot, n_nice, task_ns = row
        if not n:
            continue
        if n_proc == 0:
            results.append(fail(f"tick_health run={rid}: process ticks never recorded (workload PID unknown)"))
            bad += 1
        elif d_proc == 0 and task_ns and task_ns / 1e9 >= min_s:
            results.append(warn(f"tick_health run={rid}: process ticks did not move over {task_ns / 1e9:.1f} s"))
            bad += 1
        if n_nice and n_tot == 0:
            results.append(fail(f"tick_health run={rid}: total_ticks_end equals start in every sample (stored wrong)"))
            bad += 1
    if not bad:
        results.append(ok(f"tick_health: {len(run_ids)} runs, process ticks recorded and moving"))
    return results


def check_phase_fallback(conn, run_ids: list) -> list:
    """Runs recorded with the chunk 45 code (nice ticks present) must get direct phase
    shares; run_share_v3 there means the tick capture regressed."""
    results = []
    if not run_ids:
        return results
    if not any(r[1] == "nice_ticks_start" for r in conn.execute("PRAGMA table_info(interrupt_samples)")):
        return results
    ph = ",".join("?" * len(run_ids))
    rows = conn.execute(f"""
        SELECT oe.run_id, COUNT(*) FROM orchestration_events oe
        WHERE oe.run_id IN ({ph}) AND oe.attribution_method = 'run_share_v3'
          AND EXISTS (SELECT 1 FROM interrupt_samples i WHERE i.run_id = oe.run_id
                      AND i.nice_ticks_start IS NOT NULL)
        GROUP BY oe.run_id""", run_ids).fetchall()
    for rid, n in rows:
        results.append(warn(f"phase_fallback run={rid}: {n} phases used run_share_v3 (INFERRED) on a new run"))
    if not rows:
        results.append(ok("phase_fallback: no new run needed the run share fallback"))
    return results


def check_sample_coverage(conn, run_ids: list) -> list:
    """Energy sample coverage of the run window at least the configured minimum."""
    results = []
    if not run_ids:
        return results
    lim = _energy_consistency("min_sample_coverage_pct")
    ph = ",".join("?" * len(run_ids))
    rows = conn.execute(f"SELECT run_id, energy_sample_coverage_pct FROM runs WHERE run_id IN ({ph})",
                        run_ids).fetchall()
    low = [(r, c) for r, c in rows if c is not None and c < lim]
    unknown = [r for r, c in rows if c is None]
    for r, c in low:
        results.append(warn(f"sample_coverage run={r}: {c:.1f}% < {lim:.0f}%"))
    if unknown:
        results.append(warn(f"sample_coverage: {len(unknown)} runs without a coverage value"))
    if not low and not unknown:
        results.append(ok(f"sample_coverage: {len(rows)} runs >= {lim:.0f}%"))
    return results


def check_paper_core_query(conn) -> list:
    results = []
    rows = conn.execute("""
        SELECT ge.workflow_type, COUNT(*) AS goals,
            AVG(ge.overhead_fraction) AS avg_overhead,
            COUNT(ge.overhead_fraction) AS has_overhead
        FROM goal_execution ge
        JOIN experiments e ON ge.exp_id = e.exp_id
        WHERE e.experiment_type IN ('normal','overhead_study','retry_study',
            'failure_injection','quality_sweep','ablation')
        AND e.is_valid = 1
        GROUP BY ge.workflow_type
    """).fetchall()
    if not rows:
        results.append(fail("paper_core_query: 0 rows with is_valid=1 — no valid experiments"))
    else:
        for row in rows:
            avg = f"{row['avg_overhead']:.3f}" if row['avg_overhead'] is not None else "NULL"
            results.append(ok(f"paper_core_query: {row['workflow_type']} goals={row['goals']} avg_overhead={avg}"))
    vfv = conn.execute("SELECT COUNT(*), MAX(delta) FROM v_fraction_verification").fetchone()
    if vfv and vfv[0] > 0:
        if vfv[1] == 0.0:
            results.append(ok(f"v_fraction_verification: {vfv[0]} rows delta=0.0"))
        else:
            results.append(warn(f"v_fraction_verification: max_delta={vfv[1]}"))
    else:
        results.append(warn("v_fraction_verification: 0 rows"))
    return results


def print_energy_accounting(conn, exp_id: int, exp_type: str) -> None:
    if exp_type not in ("failure_injection", "retry_study"):
        return
    rows = conn.execute("""
        SELECT r.run_id, r.workflow_type,
            r.pkg_energy_uj, r.baseline_energy_uj, r.dynamic_energy_uj,
            r.attributed_energy_uj, r.core_energy_uj,
            r.uncore_energy_uj, r.dram_energy_uj,
            r.pre_task_energy_uj, r.post_task_energy_uj,
            r.planning_energy_uj, r.execution_energy_uj,
            r.synthesis_energy_uj, r.inter_phase_energy_uj,
            ea.orchestration_energy_uj, ea.llm_compute_energy_uj,
            ea.llm_wait_energy_uj, ea.failed_tool_energy_uj,
            ea.attribution_method,
            ge.total_energy_uj       AS goal_total,
            ge.successful_energy_uj  AS goal_success,
            ge.overhead_fraction,
            ge.orchestration_fraction,
            (SELECT SUM(ga2.energy_uj)
             FROM goal_attempt ga2
             WHERE ga2.run_id = r.run_id) AS sum_attempt_energy,
            (SELECT COUNT(*) FROM goal_attempt ga3
             WHERE ga3.goal_id = ge.goal_id AND ga3.is_retry = 1) AS retry_count
        FROM goal_execution ge
        JOIN runs r ON r.run_id = ge.winning_run_id
        JOIN energy_attribution ea ON ea.run_id = r.run_id
        WHERE ge.exp_id = ? AND ge.winning_run_id IS NOT NULL
        LIMIT 5
    """, (exp_id,)).fetchall()
    if not rows:
        return
    def _j(uj): return f"{(uj or 0)/1e6:.4f}J"
    def _p(n,d): return f"{100.0*(n or 0)/d:.1f}%" if d else "N/A"
    def _c(d,t=1000): return "✅" if abs(d)<=t else "❌"

    print(f"\n  📊 Energy Accounting (exp_id={exp_id}):")
    print(f"  {'─'*68}")
    for row in rows:
        pkg  = row["pkg_energy_uj"]        or 0
        base = row["baseline_energy_uj"]   or 0
        dyn  = row["dynamic_energy_uj"]    or 0
        attr = row["attributed_energy_uj"] or 0
        core = row["core_energy_uj"]       or 0
        unc  = row["uncore_energy_uj"]     or 0
        drm  = row["dram_energy_uj"]       or 0
        pre  = row["pre_task_energy_uj"]   or 0
        post = row["post_task_energy_uj"]  or 0
        plan = row["planning_energy_uj"]   or 0
        exe  = row["execution_energy_uj"]  or 0
        syn  = row["synthesis_energy_uj"]  or 0
        iph  = row["inter_phase_energy_uj"] or 0
        orch = row["orchestration_energy_uj"] or 0
        llmc = row["llm_compute_energy_uj"] or 0
        llmw = row["llm_wait_energy_uj"]   or 0
        ftl  = row["failed_tool_energy_uj"] or 0
        asum = row["sum_attempt_energy"]   or 0
        wf   = row["workflow_type"]        or "?"
        mth  = row["attribution_method"]   or "unknown"
        ofrc = row["orchestration_fraction"]
        gt   = row["goal_total"]           or 0
        gs   = row["goal_success"]         or 0

        print(f"  run={row['run_id']}[{wf}] retries={row['retry_count']} method={mth}")
        print(f"  [D4] E_pkg=E_core+E_uncore+E_dram:")
        _d4_terms = (row["pkg_energy_uj"], row["core_energy_uj"],
                     row["uncore_energy_uj"], row["dram_energy_uj"])
        if any(t is None for t in _d4_terms):
            print("       not applicable on this platform (a domain is unmeasured)")
        else:
            print(f"       {_j(pkg)}={_j(core)}({_p(core,pkg)})+{_j(unc)}({_p(unc,pkg)})+{_j(drm)}  {_c(pkg-(core+unc+drm))}")
        print(f"  [D3] E_pkg=E_baseline+E_dynamic:")
        print(f"       {_j(pkg)}={_j(base)}({_p(base,pkg)})+{_j(dyn)}({_p(dyn,pkg)})  {_c(pkg-(base+dyn))}")
        print(f"  [D1 Activity Partition] E_attr=E_llm_window+E_orchestration:")
        print(f"       {_j(attr)}={_j(llmc)}({_p(llmc,attr)})+{_j(orch)}({_p(orch,attr)})  {_c(attr-(llmc+orch))}")
        print(f"       [diagnostic] llm_wait={_j(llmw)}({_p(llmw,attr)}) — AXIS 3 subset of orchestration")
        if wf == "agentic":
            print(f"  [D2] E_attr=E_plan+E_exec+E_synth+E_inter:")
            print(f"       {_j(attr)}={_j(plan)}({_p(plan,attr)})+{_j(exe)}({_p(exe,attr)})+{_j(syn)}({_p(syn,attr)})+{_j(iph)}({_p(iph,attr)})  {_c(attr-(plan+exe+syn+iph))}")
        print(f"  [A2] E_attr=SUM(attempts): {_j(attr)}=={_j(asum)}  {_c(attr-asum)}")
        if gt > 0:
            print(f"  [Goal] total={_j(gt)} success={_j(gs)} overhead={_p(gt-gs,gt)}"
                  + (f" orch_frac={ofrc:.4f}" if ofrc else ""))
        if ftl > 0:
            print(f"  [Failure] failed_tool={_j(ftl)}")
        print(f"  {'─'*68}")
def main():
    parser = argparse.ArgumentParser(description="A-LEMS experiment integrity scanner")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--exp-id", type=int, help="Experiment ID to check")
    group.add_argument("--latest", action="store_true", help="Check latest experiment")
    parser.add_argument("--experiment-type", help="Filter --latest by experiment type")
    parser.add_argument("--db", default=DB_PATH, help="Path to SQLite DB")
    args = parser.parse_args()

    if not Path(args.db).exists():
        print(fail(f"DB not found: {args.db}"))
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    if args.latest:
        if args.experiment_type:
            row = conn.execute(
                "SELECT MAX(exp_id) FROM experiments WHERE experiment_type = ?",
                (args.experiment_type,)
            ).fetchone()
        else:
            row = conn.execute("SELECT MAX(exp_id) FROM experiments").fetchone()
        exp_id = row[0]
        if exp_id is None:
            print(fail("No experiments found"))
            sys.exit(1)
    else:
        exp_id = args.exp_id

    exp_meta = check_experiment(conn, exp_id)
    exp_type = exp_meta["experiment_type"] or "normal"
    workflow  = exp_meta["workflow_type"] or "comparison"

    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}A-LEMS Experiment Integrity Check{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")
    print(f"  exp_id:          {exp_id}")
    print(f"  experiment_type: {exp_type}")
    print(f"  workflow_type:   {workflow}")
    print(f"  status:          {exp_meta['status']}")
    print(f"  runs_completed:  {exp_meta['runs_completed']} / {exp_meta['runs_total']}")
    print(f"{'='*60}\n")

    run_ids     = get_run_ids(conn, exp_id)
    goal_ids    = get_goal_ids(conn, exp_id)
    attempt_ids = get_attempt_ids(conn, goal_ids)

    all_results = []
    all_results += check_runs(conn, exp_id, run_ids, exp_meta)
    all_results += check_goal_execution(conn, exp_id, goal_ids)
    all_results += check_goal_attempt(conn, goal_ids, attempt_ids)
    all_results += check_tool_failure_events(conn, attempt_ids, exp_type)
    all_results += check_normalization_factors(conn, run_ids)
    all_results += check_energy_attribution(conn, run_ids)
    all_results += check_run_quality(conn, run_ids)
    all_results += check_orchestration_events(conn, run_ids, workflow)
    all_results += check_llm_interactions(conn, run_ids)
    all_results += check_dcgm_coverage(conn, run_ids)
    all_results += check_engine_provenance(conn, exp_id)
    all_results += check_server_age(conn, run_ids)
    all_results += check_serving_snapshots(conn, run_ids)
    all_results += check_llm_ttft(conn, run_ids)
    all_results += check_gpu_mem_traffic(conn, run_ids)
    all_results += check_spans(conn, exp_id, run_ids)
    all_results += check_recording(conn, run_ids)
    all_results += check_safety_guard(conn, run_ids)
    all_results += check_expected_empty(conn)
    all_results += check_etl_queue(conn, goal_ids)
    all_results += check_retry_policy(conn)
    all_results += check_task_categories(conn)
    all_results += check_task_retry_override(conn)
    all_results += check_energy_conservation(conn, exp_id, run_ids, goal_ids)
    all_results += check_run_window(conn, run_ids)
    all_results += check_counter_samples(conn, run_ids)
    all_results += check_tick_health(conn, run_ids)
    all_results += check_phase_fallback(conn, run_ids)
    all_results += check_sample_coverage(conn, run_ids)
    all_results += check_paper_core_query(conn)
    print_energy_accounting(conn, exp_id, exp_type)    
    
    passed = sum(1 for r in all_results if r.startswith(GREEN))
    warned = sum(1 for r in all_results if r.startswith(YELLOW))
    failed = sum(1 for r in all_results if r.startswith(RED))

    for r in all_results:
        print(f"  {r}")

    print(f"\n{'='*60}")
    print(f"  RESULT: {passed} passed, {warned} warnings, {failed} failed")
    print(f"{'='*60}\n")

    conn.close()
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
