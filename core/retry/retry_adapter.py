"""
core/retry/retry_adapter.py

Pluggable retry policy adapter layer (A4, chunk 8.6).

Defines the RetryPolicyAdapter ABC and two concrete implementations:
  FlatRetryAdapter  — wraps existing RetryCoordinator.is_retryable logic (default)
  EARAdapter        — Energy-Aware Retry using calibrated cost/success profiles

Selected via YAML:
  retry_policy:
    engine: flat   # default — backward compatible
    engine: ear    # EAR — requires ear_policy_name and A3 calibration data

The retry decision point in goal_execution_manager.py calls
get_retry_adapter(args).should_retry(...) instead of
_retry_coordinator.is_retryable(...) directly.

INV-2: Adapters provide capabilities but cannot modify core measurements.
INV-3: Adapters write only to tables they own (ear_decision_log).
"""

import logging
import math
import sqlite3
from abc import ABC, abstractmethod
from typing import Any, Optional
from pathlib import Path


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ABC
# ---------------------------------------------------------------------------

class RetryPolicyAdapter(ABC):
    """
    Base class for all retry policy engines.

    should_retry() is the single decision point called by GEM after every
    failed attempt. It must be fast — it runs on the hot path between attempts.
    All DB writes (logging) must be async or deferred to avoid adding latency.
    """

    @abstractmethod
    def should_retry(
        self,
        failure_type: str,
        attempt_number: int,
        goal_id: int,
        run_context: dict,
    ) -> dict:
        """
        Decide whether to retry after a failure.

        Args:
            failure_type:   Canonical failure type string from FailureClassifier.
            attempt_number: Current attempt number (1-indexed).
            goal_id:        goal_execution.goal_id for this goal.
            run_context:    Dict with keys: conn, policy, run_id, attempt_id,
                            budget_remaining_uj (optional), ear_policy_id (EAR only).

        Returns:
            dict with keys:
              'action': 'retry' | 'abort' | 'fallback'
              'reason': str  (human-readable reason code for logging)
        """
        ...


# ---------------------------------------------------------------------------
# FlatRetryAdapter — wraps existing is_retryable logic
# ---------------------------------------------------------------------------

class FlatRetryAdapter(RetryPolicyAdapter):
    """
    Wraps RetryCoordinator.is_retryable() — identical behavior to pre-A4.
    Default adapter when retry_policy.engine is absent or 'flat'.
    Requires no DB tables beyond retry_policy (always present in core).
    """

    def __init__(self):
        # Import here to avoid circular imports — GEM already imports retry_coordinator.
        from core.execution.retry_coordinator import RetryCoordinator
        self._coordinator = RetryCoordinator()

    def should_retry(
        self,
        failure_type: str,
        attempt_number: int,
        goal_id: int,
        run_context: dict,
    ) -> dict:
        """
        Delegates to RetryCoordinator.is_retryable() unchanged.
        attempt_number and goal_id unused — flat policy does not condition on them.
        """
        policy = run_context["policy"]
        retryable = self._coordinator.is_retryable(failure_type, policy)

        if not retryable:
            return {"action": "abort", "reason": "flat_policy_not_retryable"}
        if attempt_number >= policy.max_retries + 1:
            # max_retries+1 = max_attempts; attempt_number is 1-indexed.
            return {"action": "abort", "reason": "flat_max_retries_exceeded"}
        return {"action": "retry", "reason": "flat_policy_allows"}


# ---------------------------------------------------------------------------
# EARAdapter — Energy-Aware Retry
# ---------------------------------------------------------------------------

class EARAdapter(RetryPolicyAdapter):
    """
    Energy-Aware Retry adapter.
    Conditions retry decisions on calibrated cost/success profiles from A3.

    Selected via YAML:
      retry_policy:
        engine: ear
        ear_policy_name: "ear_v1"
        budget_uj: 50000000   # optional per-goal budget

    Decision sequence (checked in order):
      1. Rule exists for this failure_type?  No  → abort (no_ear_rule_for_type)
      2. attempt_number >= rule.max_attempts? Yes → abort (ear_max_attempts_exceeded)
      3. budget_remaining < cost_threshold?  Yes → abort (ear_budget_exhausted)
      4. calibrated_success_prob < threshold? Yes → abort (ear_success_prob_too_low)
      5. All checks pass                         → rule.action (ear_allows)

    Every decision is logged to ear_decision_log asynchronously after the
    attempt completes (same Option B flush pattern as failure_injection_log).
    """

    def __init__(self, ear_policy_name: str, db_path: Path):
        self._policy_name = ear_policy_name
        self._db_path = db_path
        # Rules cache — loaded once per adapter instance (one per experiment run).
        # Key: failure_type_id, Value: rule dict.
        self._rules: Optional[dict] = None
        self._ear_policy_id: Optional[int] = None
        # Pending log rows — flushed after each attempt by GEM.
        self._pending_log: list[dict] = []

    def _load_rules(self) -> None:
        """
        Load ear_policy_rules for this policy from DB into memory cache.
        Called once on first should_retry() call.
        Logs warning and sets empty cache if policy not found.
        """
        conn = sqlite3.connect(str(self._db_path))
        try:
            row = conn.execute(
                "SELECT ear_policy_id FROM ear_policy WHERE policy_name = ?",
                (self._policy_name,),
            ).fetchone()

            if row is None:
                logger.warning(
                    "EARAdapter: policy %r not found in ear_policy table. "
                    "All decisions will be abort(no_ear_policy). "
                    "Run ear_calibrator.py --policy %r first.",
                    self._policy_name, self._policy_name,
                )
                self._rules = {}
                return

            self._ear_policy_id = row[0]
            rows = conn.execute(
                """
                SELECT failure_type_id, max_attempts, cost_threshold_uj,
                       calibrated_success_prob, success_probability_threshold,
                       action, priority
                FROM ear_policy_rules
                WHERE ear_policy_id = ?
                ORDER BY priority DESC
                """,
                (self._ear_policy_id,),
            ).fetchall()

            self._rules = {
                r[0]: {
                    "max_attempts":                 r[1],
                    "cost_threshold_uj":            r[2],
                    "calibrated_success_prob":      r[3],
                    "success_probability_threshold": r[4],
                    "action":                       r[5],
                    "priority":                     r[6],
                }
                for r in rows
            }
            logger.info(
                "EARAdapter: loaded %d rules for policy %r (id=%d)",
                len(self._rules), self._policy_name, self._ear_policy_id,
            )
        finally:
            conn.close()

    def should_retry(
        self,
        failure_type: str,
        attempt_number: int,
        goal_id: int,
        run_context: dict,
    ) -> dict:
        """
        EAR decision — see class docstring for decision sequence.
        Buffers log row to self._pending_log for flush after attempt.
        """
        # Lazy load rules on first call.
        if self._rules is None:
            self._load_rules()

        budget_remaining = run_context.get("budget_remaining_uj")
        run_id           = run_context.get("run_id")
        attempt_id       = run_context.get("attempt_id")

        rule = self._rules.get(failure_type) if self._rules else None

        # Step 1: no rule for this type.
        if rule is None:
            decision = {"action": "abort", "reason": "no_ear_rule_for_type"}
            self._buffer_log(run_id, attempt_id, failure_type, attempt_number,
                             budget_remaining, decision, rule)
            return decision

        # Step 2: attempt count check.
        if attempt_number >= rule["max_attempts"]:
            decision = {"action": "abort", "reason": "ear_max_attempts_exceeded"}
            self._buffer_log(run_id, attempt_id, failure_type, attempt_number,
                             budget_remaining, decision, rule)
            return decision

        # Step 3: budget check.
        if (budget_remaining is not None
                and rule["cost_threshold_uj"] is not None
                and budget_remaining < rule["cost_threshold_uj"]):
            decision = {"action": "abort", "reason": "ear_budget_exhausted"}
            self._buffer_log(run_id, attempt_id, failure_type, attempt_number,
                             budget_remaining, decision, rule)
            return decision

        # Step 4: success probability check.
        if (rule["calibrated_success_prob"] is not None
                and rule["success_probability_threshold"] is not None
                and rule["calibrated_success_prob"] < rule["success_probability_threshold"]):
            decision = {"action": "abort", "reason": "ear_success_prob_too_low"}
            self._buffer_log(run_id, attempt_id, failure_type, attempt_number,
                             budget_remaining, decision, rule)
            return decision

        # Step 5: all checks pass.
        decision = {"action": rule["action"], "reason": "ear_allows"}
        self._buffer_log(run_id, attempt_id, failure_type, attempt_number,
                         budget_remaining, decision, rule)
        return decision

    def _buffer_log(
        self,
        run_id: Optional[int],
        attempt_id: Optional[int],
        failure_type: str,
        attempt_number: int,
        budget_remaining: Optional[float],
        decision: dict,
        rule: Optional[dict],
    ) -> None:
        """Buffer a log row for flush after attempt completes."""
        self._pending_log.append({
            "run_id":                   run_id,
            "attempt_id":               attempt_id,
            "failure_type_id":          failure_type,
            "attempt_number":           attempt_number,
            "budget_remaining_uj":      budget_remaining,
            "action":                   decision["action"],
            "reason":                   decision["reason"],
            "calibration_cost_uj":      rule["cost_threshold_uj"] if rule else None,
            "calibration_success_prob": rule["calibrated_success_prob"] if rule else None,
        })

    def flush_decision_log(self, conn: sqlite3.Connection) -> int:
        """
        Batch INSERT pending log rows into ear_decision_log.
        Called by GEM after finish_attempt() — same Option B flush pattern
        as failure_injection_log._flush_injection_log().

        Returns number of rows flushed.
        """
        if not self._pending_log:
            return 0

        pending = list(self._pending_log)
        self._pending_log.clear()

        conn.executemany("""
            INSERT INTO ear_decision_log (
                run_id, attempt_id, failure_type_id, attempt_number,
                budget_remaining_uj, action, reason,
                calibration_cost_uj, calibration_success_prob
            ) VALUES (
                :run_id, :attempt_id, :failure_type_id, :attempt_number,
                :budget_remaining_uj, :action, :reason,
                :calibration_cost_uj, :calibration_success_prob
            )
        """, pending)
        conn.commit()

        logger.debug("EARAdapter: flushed %d decision log rows.", len(pending))
        return len(pending)

    def get_pending_log(self) -> list[dict]:
        """Return pending log rows without clearing — for inspection/testing."""
        return list(self._pending_log)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_retry_adapter(args, db_path: Optional[Path] = None) -> RetryPolicyAdapter:
    """
    Instantiate the correct RetryPolicyAdapter from parsed args.

    Args:
        args:     Parsed experiment args (from run_experiment.py / apply_config).
                  Reads: args.retry_engine, args.ear_policy_name.
        db_path:  Path to A-LEMS DB. Required for EARAdapter. Defaults to
                  get_alems_db_path() when None.

    Returns:
        FlatRetryAdapter when engine is absent or 'flat'.
        EARAdapter when engine is 'ear'.
    """
    engine = getattr(args, "retry_engine", "flat") or "flat"

    if engine == "flat":
        logger.debug("get_retry_adapter: using FlatRetryAdapter")
        return FlatRetryAdapter()

    if engine == "ear":
        policy_name = getattr(args, "ear_policy_name", None)
        if not policy_name:
            logger.error(
                "get_retry_adapter: engine=ear but ear_policy_name not set. "
                "Add retry_policy.ear_policy_name to YAML. Falling back to flat."
            )
            return FlatRetryAdapter()

        if db_path is None:
            from scripts.tools.path_loader import get_alems_db_path
            db_path = Path(get_alems_db_path())

        logger.info(
            "get_retry_adapter: using EARAdapter policy=%r db=%s",
            policy_name, db_path,
        )
        return EARAdapter(ear_policy_name=policy_name, db_path=db_path)

    logger.warning(
        "get_retry_adapter: unknown engine %r — falling back to flat.", engine
    )
    return FlatRetryAdapter()
