# core/vocabularies/agent/projections.py
# Projection helpers: write existing tables (goal_execution, goal_attempt,
# orchestration_events, recovery_events) from span context.
#
# IMPORTANT: this module does NOT replace goal_tracker.py in 39.4.
# It only adds the span_id column to existing INSERT/UPDATE calls that
# goal_tracker and experiment_runner already make.
# The SQL and commit points are UNCHANGED from goal_tracker.py.
# Full projection move is in scope for 39.5 (harness plugin split).
#
# In 39.4 the runner calls set_span_id_on_goal() and set_span_id_on_attempt()
# after the goal_tracker inserts so that new rows carry their span reference.
# Existing rows (run before 39.4) remain NULL.

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class AgentProjections:
    """
    Thin projection helper for 39.4.

    Writes span_id back onto rows already inserted by GoalTracker.
    All methods accept a db connection and the relevant IDs.
    They never open their own connection (EEI-2).
    """

    @staticmethod
    def set_span_id_on_goal(conn, goal_id: int, span_id: str) -> None:
        """
        Update goal_execution.span_id for a newly inserted goal row.

        Args:
            conn:    Active DB connection (passed from experiment_runner).
            goal_id: The goal_execution primary key just returned by GoalTracker.
            span_id: The span id opened for this goal span.
        """
        if not goal_id or not span_id:
            # Either caller did not open a span (linear run) or no goal was created.
            return
        try:
            conn.execute(
                "UPDATE goal_execution SET span_id = ? WHERE goal_id = ?",
                (span_id, goal_id),
            )
            # No commit here; caller owns the transaction boundary.
        except Exception as exc:
            # Span linkage failure must never abort the run (Rule S, additive only).
            logger.warning("AgentProjections: failed to set span_id on goal %s: %s", goal_id, exc)

    @staticmethod
    def set_span_id_on_attempt(conn, attempt_id: int, span_id: str) -> None:
        """
        Update goal_attempt.span_id for a newly inserted attempt row.

        Args:
            conn:       Active DB connection.
            attempt_id: The goal_attempt primary key just returned by GoalTracker.
            span_id:    The span id opened for this attempt span.
        """
        if not attempt_id or not span_id:
            return
        try:
            conn.execute(
                "UPDATE goal_attempt SET span_id = ? WHERE attempt_id = ?",
                (span_id, attempt_id),
            )
        except Exception as exc:
            logger.warning("AgentProjections: failed to set span_id on attempt %s: %s", attempt_id, exc)

    @staticmethod
    def set_span_id_on_run(conn, run_id: int, span_id: str) -> None:
        """
        Update runs.span_id for a newly inserted run row.

        Args:
            conn:    Active DB connection.
            run_id:  The runs primary key just returned by insert_run.
            span_id: The span id opened for this run span.
        """
        if not run_id or not span_id:
            return
        try:
            conn.execute(
                "UPDATE runs SET span_id = ? WHERE run_id = ?",
                (span_id, run_id),
            )
        except Exception as exc:
            logger.warning("AgentProjections: failed to set span_id on run %s: %s", run_id, exc)
