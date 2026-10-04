"""
goal_execution_manager.py — Retry-aware goal execution engine.

Single owner of the harness → run → goal → attempt lifecycle for one
workflow side. ExperimentRunner and test_harness delegate here when
max_retries > 0 so retry logic never leaks into callers.

Design (confirmed — do not change):
    execute_goal() owns: retry loop + goal_tracker state transitions + tool failure recording
    RunPersistenceService owns: all DB insertion + ETL chain
    No DB logic lives here — clean separation of orchestration vs persistence.

Dependency graph:
    ExperimentRunner
        ├── GoalExecutionManager   (this module)
        └── RunPersistenceService  (core/execution/run_persistence.py)

    GoalExecutionManager → RunPersistenceService  (correct direction)
    GoalExecutionManager does NOT import ExperimentRunner  (no inversion)

Energy accounting:
    goal_execution.overhead_energy_uj = total_energy_uj - successful_energy_uj
    Captures all wasted retry energy — core paper thesis signal.

Naming rationale:
    execute_goal() — research-concept name matching paper's unit of analysis.
    Not run_one_side() — that is implementation language, not research language.
"""

import logging
import time
from typing import Optional

from pathlib import Path
from core.attribution.legacy_v1 import (
    goal_execution_etl,
    energy_attribution_etl,
    phase_attribution_etl,
    duration_fix_etl,
)
from scripts.tools.path_loader import get_alems_db_path
from core.execution.retry_coordinator import RetryCoordinator, RetryPolicy
import platform
from core.execution.arm_cpu_sample_builder import _build_arm_cpu_sample_row
from core.execution.darwin_cpu_sample_builder import _build_darwin_cpu_sample_row
from core.execution.failure_classifier import FailureClassifier
from core.database.tool_failure_recorder import record_tool_failure
from core.execution.run_persistence import insert_one_run
import copy  # G140: per attempt result snapshot
from core.execution import run_persistence as _rp  # G137 stage 1 and 2
from core.execution.run_persistence import PersistenceError  # A+4, G88

logger = logging.getLogger(__name__)

# Module-level singletons — stateless, safe to share across calls
# B1: recovery_policy_id read from experiment config at runtime.
# Module-level default uses full_restart for backward compat (B1.6).
# execute_goal() passes recovery_policy_id from args when available.
_retry_coordinator = RetryCoordinator()
_failure_classifier = FailureClassifier()

# Load harness plugin groups at import, before any measurement window
# (master 5.2a, G62). Recovery is also loaded by RetryCoordinator() above;
# both calls are idempotent.
from core.recovery import RecoveryPolicyRegistry as _RecoveryRegistry
from core.telemetry.cache_collector import CacheTelemetryRegistry as _CollectorRegistry
_RecoveryRegistry.ensure_loaded()
_CollectorRegistry.ensure_loaded()

# Maps FailureClassifier types to tool_failure_events CHECK constraint values.
# tool_failure_events CHECK differs from goal_attempt failure_type — normalize here.
_TOOL_FAILURE_TYPE_MAP = {
    "timeout":          "timeout",
    "api_error":        "api_error",
    "rate_limit":       "rate_limit",
    "tool_error":       "other",
    "context_overflow": "other",
    "wrong_answer":     "other",
    "crashed":          "other",
}


def _execute_goal_impl(
    db,
    exp_id: int,
    hw_id: int,
    harness,
    executor,
    task: dict,
    workflow_type: str,
    rep_num: int,
    goal_tracker,
    policy: RetryPolicy,
    failure_injector=None,
    repetitions: int = 1,
    retry_adapter=None,
    recovery_policy_id: str = "full_restart",
    cache_collector=None,
    writer=None,
    quality_enabled: bool = False,
    _state=None,
) -> Optional[int]:
    """
    Execute one goal (one workflow side) with full retry support.

    Flow per attempt:
        start_attempt() → [inject?] → harness.run_*() → classify →
        record_tool_failure() → insert_one_run() → finish_attempt() →
        [retry if policy allows] → finish_goal() → ETL

    Args:
        db:               DB adapter.
        exp_id:           Parent experiment ID.
        hw_id:            Hardware profile ID.
        harness:          ExperimentHarness instance.
        executor:         LinearExecutor or AgenticExecutor.
        task:             Task dict — keys: id, name, prompt, meta, tool_graph.
        workflow_type:    'linear' or 'agentic' — never 'comparison'.
        rep_num:          Repetition number (1-indexed) for run_number field.
        goal_tracker:     GoalTracker instance — owns all DB state transitions.
        policy:           Resolved RetryPolicy for this goal.
        failure_injector: FailureInjector or None. Only active for injection studies.

    Returns:
        goal_id (int) or None on unrecoverable failure.
    """
    if workflow_type not in ("linear", "agentic"):
        logger.warning("execute_goal: invalid workflow_type=%r — aborting", workflow_type)
        return None

    from core.storage.inprocess_writer import InProcessWriter as _IPW
    from core.storage.resolver import resolve_store as _rs
    _own_writer = writer is None
    if _own_writer:
        writer = _IPW(_rs())
        writer.open()
    conn        = writer.conn
    # A+ (G137): closed attempt windows wait here until the goal ends; the
    # wrapper reads the same list if the goal raises.
    if _state is None:
        _state = {"measured": [], "conn": None, "goal_id": None}
    _state["conn"] = conn
    measured = _state["measured"]
    task_id     = task.get("id", "unknown")
    task_name   = task.get("name", task_id)
    task_meta   = task.get("meta", {}) or {}
    task_prompt = task.get("prompt", "")
    is_cloud    = not executor.config.get("is_local", False)

    # One goal_execution row covers all retry attempts on this workflow side.
    # first_run_id=-1 — unknown at start, goal_tracker.finish_goal() updates it.
    goal_id = goal_tracker.start_goal(
        conn=conn,
        exp_id=exp_id,
        task_id=task_id,
        task_name=task_name,
        goal_type=task_meta.get("category", "custom"),
        workflow_type=workflow_type,
        difficulty_level=task_meta.get("level"),
        first_run_id=-1,
    )
    if goal_id is None:
        logger.warning("execute_goal: start_goal returned None — aborting")
        return None
    _state["goal_id"] = goal_id
 
    # Wire exp_id into injector now that experiment exists in DB.
    # SHA-256 stable seed requires exp_id — must be set before any injection calls.
    if failure_injector is not None and workflow_type == "agentic" and hasattr(failure_injector, "set_exp_id"):
        # Compute exact draw count — total attempts this goal will make.
        # timeout draws = max_attempts (one per attempt).
        # tool draws estimated from task tool_calls field — 0 for pure LLM tasks.
        max_attempts    = policy.max_retries + 1
        tool_calls      = task_meta.get("tool_calls", 0) or 0
        exact_draws     = max_attempts + (max_attempts * tool_calls)
        failure_injector.set_exp_id(exp_id, total_draws=exact_draws * repetitions)

    max_attempts    = policy.max_retries + 1
    # A4: use passed-in adapter or fall back to FlatRetryAdapter.
    from core.retry.retry_adapter import FlatRetryAdapter
    _retry_adapter = retry_adapter if retry_adapter is not None else FlatRetryAdapter()
    prev_attempt_id = None
    winning_run_id  = None
    all_run_ids     = []
    attempts_made   = 0
    winning_result  = None
    last_result     = None
    failed_attempt_events = []  # accumulate tool errors from failed attempts
    # goal_attempt.energy_uj = E_attributed per attempt (process share of workload)
    # goal_execution.total_energy_uj = SUM(E_attributed across all attempts)
    # This is the paper unit of analysis — not E_dynamic which includes background

    for attempt_num in range(1, max_attempts + 1):
        is_retry    = attempt_num > 1
        attempts_made += 1

        attempt_id = goal_tracker.start_attempt(
            conn=conn,
            goal_id=goal_id,
            attempt_number=attempt_num,
            is_retry=is_retry,
            retry_of_attempt_id=prev_attempt_id if is_retry else None,
        )
        if attempt_id is None:
            logger.warning(
                "execute_goal: start_attempt returned None goal=%d attempt=%d",
                goal_id, attempt_num,
            )
            break

        result       = None
        failure_type = None
        outcome      = "failure"
        run_id       = None

        try:
            # Injection happens AFTER harness runs — never before.
            # Every LLM execution consumes real RAPL energy regardless of outcome.
            # Timeout and tool failures are injected into the result dict post-harness.
            pass

            if workflow_type == "linear":
                result = harness.run_linear(
                    executor=executor,
                    prompt=task_prompt,
                    task_id=task_id,
                    is_cloud=is_cloud,
                    run_number=rep_num,
                )
            else:
                # Pass failure_injector into executor so _dispatch_tool can
                # inject tool failures during actual tool execution — after
                # harness starts and energy measurement is running
                if failure_injector is not None:
                    executor.failure_injector = failure_injector
                    # G139: a repetition number is never a run id (C6); run ids
                    # exist only after persistence (EEI-4).
                    executor._current_rep_num = rep_num
                    executor._current_attempt = attempt_num
                # Bug 7 fix: set attempt_id on executor so _emit_event
                # tags every orchestration event with the correct attempt boundary.
                executor._current_attempt_id = attempt_id
                result = harness.run_agentic(
                    executor=executor,
                    task=task_prompt,
                    task_id=task_id,
                    is_cloud=is_cloud,
                    run_number=rep_num,
                    tool_graph=task.get("tool_graph"),
                )

        except Exception as exc:
            # Harness raised — classify and record before moving to finish_attempt
            failure_type = _failure_classifier.classify(exception=exc)
            logger.warning(
                "execute_goal: goal=%d attempt=%d raised %s → %s",
                goal_id, attempt_num, type(exc).__name__, failure_type,
            )
            _record_attempt_failure(conn, attempt_id, goal_id, failure_type, str(exc))

        if result is not None:
            # Post-harness injection — result exists, energy captured, now inject failure.
            # ScenarioInjector: should_inject() with phase='post_harness'.
            # Legacy FailureInjector: maybe_inject_timeout() path (backward compat).
            if failure_injector and workflow_type == "agentic" and failure_injector.is_active():
                _inject_post = False
                _ftype_post  = None
                if hasattr(failure_injector, "should_inject"):
                    # ScenarioInjector path
                    _draw_post = getattr(failure_injector, "_draw_counter", 0) + 1
                    failure_injector._draw_counter = _draw_post
                    _inject_post, _ftype_post = failure_injector.should_inject(
                        step_index=0,
                        phase="post_harness",
                        tool_name=None,
                        draw_number=_draw_post,
                        attempt_id=attempt_id,
                        goal_id=goal_id,
                    )
                else:
                    # Legacy FailureInjector path
                    _inject_post = failure_injector.maybe_inject_timeout(
                        rep_num=rep_num, attempt_num=attempt_num
                    )
                    _ftype_post = "timeout"

                if _inject_post and _ftype_post:
                    from core.injection.failure_simulator import (
                        simulate_post_harness_failure, NON_INJECTABLE_TYPES
                    )
                    if _ftype_post in NON_INJECTABLE_TYPES:
                        logger.error(
                            "ScenarioInjector: %r is non-injectable "
                            "(reasoning domain) — skipping post-harness injection",
                            _ftype_post,
                        )
                    else:
                        logger.info(
                            "ScenarioInjector: post-harness INJECT type=%s "
                            "goal=%d attempt=%d",
                            _ftype_post, goal_id, attempt_num,
                        )
                        result["execution"] = simulate_post_harness_failure(_ftype_post)

            # result["execution"] is the full executor output dict.
            # The structured failure metadata lives one level deeper at
            # result["execution"]["execution"] (set by agentic.execute()).
            # result["execution"] is the full executor output dict.
            # The actual failure metadata is always at ["execution"]["execution"].
            # Never fall back to _outer — it has status="success" at top level
            # from pending_interactions which would mask injection failures.
            _outer = result.get("execution", {}) or {}
            exec_dict = _outer.get("execution", _outer) or {}
            # A5: agentic success path never sets status key — absent = success.
            # "failure"/"failed"/"partial_failure" are explicit failure signals.
            # Default to "success" so agentic retry attempts are not silently
            # downgraded to failure when injection does not fire.
            _status = exec_dict.get("status", "success")
            if _status in ("failure", "failed"):
                outcome = "failure"
            elif _status == "partial_failure":
                # Synthesis succeeded but some steps failed — classify as failure
                # for goal tracking; paper can filter by failed_steps > 0
                outcome = "failure"
            else:
                outcome = "success"

            # Also classify when outcome=success but error_message present —
            # groq 429 and context_overflow return no status key but have
            # error text in execution.error_message. Without this check,
            # LLM-level failures are invisible to tool_failure_events.
            if failure_type is None:
                failure_type = _failure_classifier.classify(run_result=result)
                logger.warning(
                    "DEBUG classify: attempt=%d failure_type=%s",
                    attempt_id, failure_type,
                )
                if failure_type and failure_type not in ("crashed", "wrong_answer"):
                    logger.warning(
                        "DEBUG recording tool failure attempt=%d type=%s",
                        attempt_id, failure_type,
                    )
                    _record_attempt_failure(
                        conn, attempt_id, goal_id, failure_type,
                        str(exec_dict.get("error_message", "")),
                    )
                    # Downgrade outcome — harness said success but LLM failed
                    if outcome == "success":
                        outcome = "failure"

            # Persist run — all attempts with data get a run_id for energy accounting
            # Capture per-attempt energy for goal_attempt snapshot.
            # runs row inserted ONCE after loop — not here.
            # energy_uj stored in goal_attempt for ETL overhead calculation.

        # Safety net — outcome=failure must always have failure_type populated.
        # Prevents NULL failure_type in goal_attempt for any failure path.
        if outcome != "success" and failure_type is None:
            # "other" removed from taxonomy in A1 — tool_error is the
            # correct structural catch-all for unclassified failures.
            failure_type = "tool_error"

        # Accumulate per-attempt energy into running total
        if result is not None:
            ml = result.get("ml_features", {}) or {}


        # E4: same rule as save_pair and save_single.
        from core.execution.run_persistence import (
            attributed_energy_or_none, orchestration_energy_or_none)
        energy_uj = attributed_energy_or_none(result)
        orchestration_uj = orchestration_energy_or_none(result)

        goal_tracker.finish_attempt(
            conn=conn,
            attempt_id=attempt_id,
            run_id=None,        # run_id unknown until after loop — updated below
            outcome=outcome,
            energy_uj=energy_uj,
            orchestration_uj=orchestration_uj,
            compute_uj=None,
            failure_type=failure_type,
        )
        # A+ eligibility: the harness returned, so t1 was reached and the
        # window is closed. Pre t0 failures (result None) get no run.
        if result is not None:
            # G140: the harness reuses inner objects between calls (point reads,
            # timing, CPU summaries); a later attempt would overwrite this one's
            # values. Keep an independent snapshot taken when the window closed.
            measured.append({"attempt_id": attempt_id, "attempt_num": attempt_num,
                             "outcome": outcome, "result": copy.deepcopy(result),
                             "run_id": None})
 
        # Flush ScenarioInjector pending log to failure_injection_log.
        # Batch INSERT after finish_attempt() — attempt_id is now committed.
        # run_id is NULL here; ETL backfills it after runs row is created.
        # Crash before this point loses audit rows for this attempt — documented
        # tradeoff (A2 design decision: hot-path latency vs persistence).
        if failure_injector is not None and hasattr(failure_injector, "get_pending_log"):
            _flush_injection_log(conn, failure_injector, attempt_id, goal_id)
 
        prev_attempt_id = attempt_id
        if result is not None:
            last_result = result
        if outcome != "success" and result is not None:
            _fevs = result.get("orchestration_events") or []
            for ev in _fevs:
                _meta = ev.get("metadata") or {}
                if ev.get("event_type") == "tool_call" and not _meta.get("success", True):
                    failed_attempt_events.append({
                        "event_type": "tool_error",
                        "ts_ns": ev.get("start_time_ns") or 0,
                        "attributes": {
                            "tool_name": _meta.get("tool_name") or _meta.get("tool"),
                            "attempt_number": attempt_num,
                            "error": _meta.get("error", "unknown"),
                        },
                    })
        if outcome == "success":
            winning_result = result
            logger.info(
                "execute_goal: goal=%d succeeded attempt=%d",
                goal_id, attempt_num,
            )
            break

        # Non-retryable — stop immediately, preserve energy for next goal
        # A4: adapter decision replaces flat is_retryable check.
        if failure_type:
            _run_context = {
                "policy":              policy,
                "conn":                conn,
                "run_id":              run_id,
                "attempt_id":          attempt_id,
                "budget_remaining_uj": None,
            }
            _retry_decision = _retry_adapter.should_retry(
                failure_type, attempt_num, goal_id, _run_context,
            )
            # Flush EAR decision log after attempt (Option B flush pattern).
            if hasattr(_retry_adapter, "flush_decision_log"):
                _retry_adapter.flush_decision_log(conn)
            if _retry_decision["action"] != "retry":
                logger.info(
                    "execute_goal: goal=%d adapter=%s action=%s reason=%s attempt=%d",
                    goal_id, type(_retry_adapter).__name__,
                    _retry_decision["action"], _retry_decision["reason"], attempt_num,
                )
                break

        if attempt_num >= max_attempts:
            logger.info(
                "execute_goal: goal=%d exhausted %d attempts",
                goal_id, max_attempts,
            )
            break

        # B1: record recovery event before next attempt starts.
        # All variables in scope: conn, attempt_id, goal_id, failure_type.
        try:
            import time as _time_b1
            # Read from in-memory result buffer — DB insert happens after loop.
            # get_trajectory() would return empty at this point (timing gap).
            _trajectory = []
            if result is not None:
                _trajectory = (
                    result.get("orchestration_events") or
                    result.get("execution", {}).get("events") or
                    result.get("events") or
                    []
                )
            _total_steps = len(_trajectory)
            # Read recovery policy from args if available, else use module default.
            from core.recovery import RecoveryPolicyRegistry
            _recovery_policy = RecoveryPolicyRegistry.get(recovery_policy_id)
            _recovery_decision = _recovery_policy.decide(
                failure_type=failure_type or "tool_error",
                failure_step=_total_steps,
                trajectory=_trajectory,
                attempt_context={
                    "attempt_id": attempt_id,
                    "goal_id": goal_id,
                    "attempt_number": attempt_num,
                },
            )
            _replayed = (
                _total_steps - _recovery_decision.rollback_target_step
                if _total_steps > 0 else None
            )
            _replay_frac = (
                _replayed / _total_steps
                if (_total_steps > 0 and _replayed is not None) else None
            )
            _recovery_id = goal_tracker.record_recovery_event(
                conn=conn,
                attempt_id=attempt_id,
                goal_id=goal_id,
                failure_type_id=failure_type or "tool_error",
                recovery_strategy=_recovery_decision.strategy,
                rollback_depth_turns=_recovery_decision.rollback_depth_turns,
                total_trajectory_steps=_total_steps or None,
                replayed_steps=_replayed,
                replay_fraction=_replay_frac,
                recovery_point_step=_recovery_decision.rollback_target_step,
                recovery_point_phase=_recovery_decision.recovery_point_phase,
                recovery_start_ns=_time_b1.time_ns(),
            )
            # B2: collect cache telemetry for this recovery.
            # NoOpCollector returns ([], []) on all current platforms — correct per B2.5.
            # Runner inserts returned rows; collector never writes DB (INV-2).
            # B3: use EngineBackedCollector if runner wired one, else NoOp.
            _collector = cache_collector
            if _collector is None:
                from core.telemetry.cache_collector import CacheTelemetryRegistry
                _collector = CacheTelemetryRegistry.get(None)
            _collect_result = _collector.collect(
                run_id=None,
                attempt_id=attempt_id,
                recovery_id=_recovery_id,
                request_context={},
            )
            if len(_collect_result) == 3:
                _reuse_events, _cache_snapshots, _rt_snapshots = _collect_result
            else:
                _reuse_events, _cache_snapshots = _collect_result
                _rt_snapshots = []
            for _sre in _reuse_events:
                _sre.run_id = None
                _sre.attempt_id = attempt_id
                _sre.recovery_id = _recovery_id
                conn.execute(
                    """INSERT INTO state_reuse_events
                       (recovery_id, attempt_id, run_id, reuse_type, reuse_source,
                        tokens_reused, tokens_recomputed, reuse_fraction,
                        cache_hit, cache_query_time_ns, state_size_bytes)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (_sre.recovery_id, _sre.attempt_id, _sre.run_id,
                     _sre.reuse_type, _sre.reuse_source, _sre.tokens_reused,
                     _sre.tokens_recomputed, _sre.reuse_fraction,
                     _sre.cache_hit, _sre.cache_query_time_ns, _sre.state_size_bytes),
                )
            for _css in _cache_snapshots:
                _css.run_id = None
                _css.attempt_id = attempt_id
                conn.execute(
                    """INSERT INTO cache_state_snapshots
                       (run_id, attempt_id, timestamp_ns, engine_name, cache_type,
                        capacity_tokens, occupied_tokens, occupancy_fraction,
                        hit_rate_aggregate)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (_css.run_id, _css.attempt_id, _css.timestamp_ns,
                     _css.engine_name, _css.cache_type, _css.capacity_tokens,
                     _css.occupied_tokens, _css.occupancy_fraction,
                     _css.hit_rate_aggregate),
                )
            for _srs in _rt_snapshots:
                conn.execute(
                    """INSERT INTO serving_runtime_snapshots
                       (run_id, attempt_id, timestamp_ns, engine_name, engine_type,
                        snapshot_type, telemetry_scope,
                        kv_capacity_tokens, kv_occupied_tokens,
                        kv_occupancy_fraction, kv_hit_rate_aggregate,
                        kv_num_evictions,
                        tier_vram_bytes, tier_ram_bytes, tier_disk_bytes,
                        tier_vram_fraction, tier_ram_fraction, tier_disk_fraction,
                        queue_active, queue_waiting, queue_completed, queue_rejected,
                        tokens_per_second, ttft_ms,
                        prompt_tokens_total, generation_tokens_total,
                        extra_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (_srs.run_id, _srs.attempt_id, _srs.timestamp_ns,
                     _srs.engine_name, _srs.engine_type,
                     _srs.snapshot_type, _srs.telemetry_scope,
                     _srs.kv_capacity_tokens, _srs.kv_occupied_tokens,
                     _srs.kv_occupancy_fraction, _srs.kv_hit_rate_aggregate,
                     _srs.kv_num_evictions,
                     _srs.tier_vram_bytes, _srs.tier_ram_bytes, _srs.tier_disk_bytes,
                     _srs.tier_vram_fraction, _srs.tier_ram_fraction, _srs.tier_disk_fraction,
                     _srs.queue_active, _srs.queue_waiting,
                     _srs.queue_completed, _srs.queue_rejected,
                     _srs.tokens_per_second, _srs.ttft_ms,
                     _srs.prompt_tokens_total, _srs.generation_tokens_total,
                     _srs.extra_json),
                )
            if _reuse_events or _cache_snapshots or _rt_snapshots:
                conn.commit()
        except Exception as _b1_exc:
            # Never let telemetry break the retry loop.
            logger.warning("B1/B2: recovery telemetry failed: %s", _b1_exc)

        # Backoff only when another attempt will follow — never sleep at loop end
        if policy.backoff_seconds > 0:
            logger.debug(
                "execute_goal: goal=%d backing off %.1fs before retry",
                goal_id, policy.backoff_seconds,
            )
            time.sleep(policy.backoff_seconds)

    # Insert ONE runs row — winning result if exists, else last valid result.
    # UNIQUE(exp_id, run_number, workflow_type) preserved — one row per workflow side.
    # Per-attempt energy lives in goal_attempt.energy_uj — ETL aggregates overhead.
    # Log injection summary — verify min_failures constraint met
    if failure_injector is not None and hasattr(failure_injector, "injection_summary"):
        summary = failure_injector.injection_summary()
        if not summary.get("min_met", True):
            logger.warning(
                "execute_goal: goal=%d injection min_failures not met — "
                "injected=%d required=%d. Increase repetitions or use "
                "deterministic_validation mode.",
                goal_id, summary["total_injected"], summary.get("min_failures", 0),
            )
        else:
            logger.info(
                "execute_goal: goal=%d injection complete — injected=%d",
                goal_id, summary["total_injected"],
            )
     
    final_result = winning_result or last_result
    # G137 A+: every closed attempt window becomes its own run (C1, C3);
    # attempt energy is read back from its own run (C4).
    winning_run_id, all_run_ids, _persist_failed = _persist_attempts(
        db, conn, exp_id, hw_id, goal_id, workflow_type, rep_num, task, measured,
    )


    # All persistence for this run happened in insert_one_run above
    # (RunPersistenceService): samples once, events once, then ETL (G82).

    # finish_goal always called — regardless of outcome or exception path
    goal_tracker.finish_goal(
        conn=conn,
        goal_id=goal_id,
        success=winning_run_id is not None,
        winning_run_id=winning_run_id,
        total_attempts=attempts_made,  # all started attempts, not just persisted runs
    )

    # ETL — goal energy rollup after all attempts recorded
    goal_execution_etl.process_one(goal_id, conn)

    # A+4: committed attempts stay; the caller must not report success.
    if _persist_failed:
        raise PersistenceError(
            "INCOMPLETE_PERSISTENCE goal=%d attempts=%s" % (goal_id, _persist_failed)
        )

    # goal_output and quality scores: the same call save_pair and save_single
    # make, after the goal ETL so attempt ids are committed (G76).
    # quality is goal scoped: recorded on the winning attempt's run (39.5.2c).
    _recs = [m.pop("stages", None) for m in measured]
    _win = next((r for r in _recs if r is not None and r.run_id == winning_run_id), None)
    try:
        if final_result is not None:
            from core.execution.experiment_runner import _run_quality_scoring  # late: cycle
            with stage_or_noop(_win, "quality"):
                _run_quality_scoring(db=db, goal_id=goal_id, result=final_result,
                                     workflow_type=workflow_type, conn=conn,
                                     quality_enabled=quality_enabled)
    finally:
        for _r in _recs:
            if _r is None:
                continue
            # skip merges harmlessly into a quality row already recorded
            _r.skip("quality", "no_result" if _r is _win else "goal_scored_on_winner")
            persist_after_run(_r, db)
    # ETL runs synchronously above — queue_etl removed to prevent
    # pending entries that never get marked done (N40 fix)
 
    # G140: per run attribution, phase attribution and duration fix already ran
    # in _persist_attempts stage 2 (run_derived) with each attempt's own result.
    # The old loop here re-applied the final attempt's point reads and durations
    # to every run of the goal (cross attempt values in a run row, C1 violation).

    if _own_writer:
        writer.close()
    return goal_id

def execute_goal(
    db,
    exp_id: int,
    hw_id: int,
    harness,
    executor,
    task: dict,
    workflow_type: str,
    rep_num: int,
    goal_tracker,
    policy,
    failure_injector=None,
    repetitions: int = 1,
    retry_adapter=None,
    recovery_policy_id: str = "full_restart",
    cache_collector=None,
    writer=None,
    quality_enabled: bool = False,
) -> Optional[int]:
    """
    Execute one goal with retries; A+ persistence (DESIGN_39_5_1_G137_v3).

    Attempts are measured back to back with no I/O between them. If the goal
    raises, every attempt already measured is persisted raw (stage 1) and the
    original exception is re raised unchanged; persistence errors are logged
    and never replace it (A+4).

    Returns:
        goal_id (int) or None on unrecoverable failure.
    """
    state = {"measured": [], "conn": None, "goal_id": None}
    try:
        return _execute_goal_impl(
            db, exp_id, hw_id, harness, executor, task, workflow_type, rep_num,
            goal_tracker, policy, failure_injector=failure_injector,
            repetitions=repetitions, retry_adapter=retry_adapter,
            recovery_policy_id=recovery_policy_id, cache_collector=cache_collector,
            writer=writer, quality_enabled=quality_enabled, _state=state,
        )
    except BaseException:
        pending = [m for m in state["measured"]
                   if m["run_id"] is None and not m.get("failed")]
        if pending and state["conn"] is not None:
            try:
                _persist_attempts(db, state["conn"], exp_id, hw_id, state["goal_id"],
                                  workflow_type, rep_num, task, state["measured"],
                                  derived=False)
            except Exception as perr:  # never mask the original exception
                logger.error("execute_goal: raw persistence on exception path failed: %s", perr)
        # G100 (new rows): a goal that raised must not stay 'running'. Close it as
        # failed with the attempts already measured; never mask the original error.
        if state["conn"] is not None and state["goal_id"] is not None:
            try:
                goal_tracker.finish_goal(
                    conn=state["conn"], goal_id=state["goal_id"], success=False,
                    winning_run_id=None, total_attempts=len(state["measured"]),
                )
            except Exception as gerr:
                logger.error("execute_goal: closing goal on exception path failed: %s", gerr)
        raise


from core.observability.stages import StageRecorder, persist_after_run, stage_or_noop


def _persist_stage_rows(db, measured):
    """Phase 1: write each attempt's finished stages; recorders stay for quality."""
    for m in measured:
        persist_after_run(m.get("stages"), db, final=False)


def _persist_attempts(db, conn, exp_id, hw_id, goal_id, workflow_type, rep_num,
                      task, measured, derived=True):
    """
    Persist every closed attempt window as its own run (G137 section 4).

    Stage 1 per attempt: run row, samples, events (one commit each), then the
    attempt is linked by attempt_id. Stage 2 per committed run: derived steps,
    spans, attempt energy read back from the run, event attempt link.

    Returns:
        (winning_run_id, run_ids, failed_attempt_ids)
    """
    failed = []
    trace = {"trace_id": None, "root_span_id": None}  # one trace per goal
    for m in measured:
        if m["run_id"] is not None or m.get("failed"):
            continue
        m["result"]["task_meta"] = task  # same task_meta as save_pair (G86)
        # C-EV stage recorder per attempt run (39.5.2c).
        m["stages"] = StageRecorder(
            "execute_goal", run_uid=m["result"].get("ml_features", {}).get("global_run_id"))
        try:
            m["run_id"] = _rp.persist_raw(db, exp_id, hw_id, m["result"], workflow_type, rep_num,
                                          stages=m["stages"])
            m["stages"].attach_run_id(m["run_id"])
            # Link by attempt_id only; never by goal_id (G137 section 6).
            conn.execute("UPDATE goal_attempt SET run_id = ? WHERE attempt_id = ?",
                         (m["run_id"], m["attempt_id"]))
            conn.commit()
        except Exception as exc:
            m["failed"] = True
            failed.append(m["attempt_id"])
            logger.error("execute_goal: goal=%s attempt=%d INCOMPLETE_PERSISTENCE: %s",
                         goal_id, m["attempt_num"], exc)
    run_ids = [m["run_id"] for m in measured if m["run_id"] is not None]
    winning = next((m["run_id"] for m in measured
                    if m["outcome"] == "success" and m["run_id"] is not None), None)
    if not derived:
        _persist_stage_rows(db, measured)
        return winning, run_ids, failed

    for m in measured:
        rid = m["run_id"]
        if rid is None:
            continue
        try:
            _rp.run_derived(db, rid, m["result"], stages=m.get("stages"))
        except Exception as exc:  # raw stays committed (A+3)
            logger.error("execute_goal: run=%d derived steps failed: %s", rid, exc)
            continue
        with stage_or_noop(m.get("stages"), "spans"):
            _write_attempt_spans(db, conn, rid, m, workflow_type, goal_id, trace)
        # C4: attempt energy comes from its own committed, attributed run.
        conn.execute(
            "UPDATE goal_attempt SET energy_uj = "
            "(SELECT attributed_energy_uj FROM runs WHERE run_id = ?) WHERE attempt_id = ?",
            (rid, m["attempt_id"]))
        conn.execute(
            "UPDATE orchestration_events SET attempt_id = ? WHERE run_id = ? AND attempt_id IS NULL",
            (m["attempt_id"], rid))
        conn.commit()
    _persist_stage_rows(db, measured)
    return winning, run_ids, failed


def _write_attempt_spans(db, conn, run_id, m, workflow_type, goal_id, trace):
    """
    Spans of one attempt run inside the goal's single trace.

    The first attempt's run span is the trace root; later attempts' run spans
    are its children, so a goal trace has one root (G136, goal part).
    Never raises: spans are observability, not measurement.
    """
    try:
        import uuid
        from core.vocabularies.agent.span_writer import SpanWriter
        from core.vocabularies.agent.span_builder import build_spans_from_result
        from core.execution.experiment_runner import _backfill_span_outcome  # late: cycle
        if trace["trace_id"] is None:
            trace["trace_id"] = uuid.uuid4().hex
        writer = SpanWriter(trace["trace_id"])
        span_id = writer.open_span(
            "run", f"{workflow_type}:goal:{goal_id}:attempt:{m['attempt_num']}",
            parent_span_id=trace["root_span_id"])
        writer.close_span(span_id)
        if trace["root_span_id"] is None:
            trace["root_span_id"] = span_id
        hw = db.get_hardware_info() if hasattr(db, "get_hardware_info") else {}
        build_spans_from_result(writer, span_id, m["result"], workflow_type, hw)
        with db.transaction():
            writer.flush_to_db(db, run_id)
        attempt_span = next((r for r in writer._spans if r.kind == "attempt"), None)
        if attempt_span is not None:
            conn.execute("UPDATE goal_attempt SET span_id = ? WHERE attempt_id = ?",
                         (attempt_span.span_id, m["attempt_id"]))
            conn.commit()
        _backfill_span_outcome(conn, run_id, m["outcome"])
    except Exception as exc:
        logger.warning("execute_goal: spans for run=%d failed: %s", run_id, exc)


def _flush_injection_log(
    conn,
    injector,
    attempt_id: int,
    goal_id: int,
) -> None:
    """
    Batch INSERT pending ScenarioInjector decisions to failure_injection_log.
 
    Called after finish_attempt() — attempt_id is committed at this point.
    run_id is NULL; ETL backfills after runs row is created.
    Existing FailureInjector has no get_pending_log() — guarded by hasattr above.
 
    Persistence contract: a process crash before this call loses audit rows
    for the current attempt. Injection behavior already fired — only log is lost.
    """
    try:
        pending = injector.get_pending_log()
        if not pending:
            return
        conn.executemany(
            """
            INSERT INTO failure_injection_log (
                run_id, attempt_id, goal_id,
                scenario_id, rule_index, injected_type,
                target_step, target_phase, target_tool,
                injection_time_ns, draw_number, random_seed,
                injection_algorithm_version, status, skip_reason
            ) VALUES (
                :run_id, :attempt_id, :goal_id,
                :scenario_id, :rule_index, :injected_type,
                :target_step, :target_phase, :target_tool,
                :injection_time_ns, :draw_number, :random_seed,
                :injection_algorithm_version, :status, :skip_reason
            )
            """,
            [{**row, "attempt_id": attempt_id, "goal_id": goal_id} for row in pending],
        )
        conn.commit()
        logger.debug(
            "_flush_injection_log: flushed %d rows attempt=%d goal=%d",
            len(pending), attempt_id, goal_id,
        )
        # Bug 13 fix: write tool_failure_events for injected rows.
        # failure_injection_log alone is not sufficient — cost profiling
        # (A3) reads tool_failure_events for wasted_energy_uj attribution.
        # Only status=injected rows produced real failures; status=skipped
        # rows fired no injection and must not create failure event rows.
        for row in pending:
            if row.get("status") != "injected":
                continue
            injected_type = row.get("injected_type") or "tool_error"
            target_tool = row.get("target_tool") or "harness"
            # injected_type is already a taxonomy-valid string (ScenarioInjector
            # validates against failure_taxonomy at load time). Do NOT pass through
            # _TOOL_FAILURE_TYPE_MAP — that map predates A1 and maps tool_error→other
            # which is no longer in the taxonomy.
            record_tool_failure(
                conn=conn,
                attempt_id=attempt_id,
                goal_id=goal_id,
                tool_name=target_tool,
                failure_type=injected_type,
                failure_phase="execution",
                error_message=f"INJECTED[{injected_type}]: scenario {row.get('scenario_id')} rule {row.get('rule_index')}",
                retry_attempted=0,
                retry_success=0,
            )
    except Exception as exc:
        # Never crash the experiment over audit log failure.
        logger.error(
            "_flush_injection_log: failed attempt=%d: %s", attempt_id, exc
        )

def _record_attempt_failure(
    conn,
    attempt_id: int,
    goal_id: int,
    failure_type: str,
    error_message: str,
    retry_attempted: int = 0,
    tools_used: list = None,
) -> None:
    """
    Insert one tool_failure_events row for a failed attempt.
    Called for both exception-path and result-path failures.
    tool_name='harness' is pragmatic — future chunks split by provider/planner/tool_dispatch.
    wasted_energy_uj is NULL at insert — energy_attribution_etl backfills after run completes.
    retry_attempted=1 for non-first attempts — tracks retry cost in tool_failure_events.
    """
    record_tool_failure(
        conn=conn,
        attempt_id=attempt_id,
        goal_id=goal_id,
        tool_name=(tools_used[0] if tools_used else "harness"),
        failure_type=failure_type,
        failure_phase="execution",
        error_message=error_message,
        retry_attempted=retry_attempted,
        retry_success=0,
    )


