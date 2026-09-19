"""
================================================================================
injection_engine.py — InjectionEngine ABC
================================================================================

Defines the interface every injection engine must implement.
ScenarioInjector is the canonical implementation (scenario_injector.py).
The existing FailureInjector (core/execution/failure_injector.py) is NOT
a subclass — it predates this ABC and is kept for backward compat (SC-5).
The factory in scenario_loader.py routes mode=scenario to ScenarioInjector
and all other modes to the existing FailureInjector.

Persistence contract:
    Injection engines do NOT own DB persistence.
    should_inject() buffers decisions in self._pending_log (list of dicts).
    get_pending_log() returns the buffer and clears it.
    The execution manager (goal_execution_manager.py) calls get_pending_log()
    after finish_attempt() and batch-INSERTs to failure_injection_log.
    This keeps DB lifecycle in the execution manager, not the injector.

Crash tradeoff:
    Decisions are made synchronously (no latency in hot path).
    Persistence is buffered until attempt completion.
    A process crash before flush loses audit rows for that attempt.
    The actual injection behavior is not lost — it already fired.

Author: Deepak Panigrahy
SPEC: 8.6-A2
================================================================================
"""

import time
from abc import ABC, abstractmethod
from typing import Optional


class InjectionEngine(ABC):
    """
    Base class for all failure injection engines.

    Subclasses implement should_inject() with their own decision logic.
    All other methods have default implementations that delegate to the
    two abstract methods, so simple subclasses only need to implement
    should_inject() and get_algorithm_version().

    Lifecycle per attempt:
        set_exp_id()          called once after experiment row created in DB
        should_inject() x N   called per tool dispatch or timeout check
        get_pending_log()     called by execution manager after finish_attempt()
        flush is external     execution manager owns the INSERT
    """

    def __init__(self) -> None:
        # Buffered injection decisions — flushed by execution manager.
        # Never written to DB inside the engine.
        self._pending_log: list = []
        self._enabled: bool = False

    # ── Abstract interface ────────────────────────────────────────────────────

    @abstractmethod
    def should_inject(
        self,
        step_index: int,
        phase: str,
        tool_name: Optional[str],
        draw_number: int,
        attempt_id: int,
        goal_id: int,
    ) -> tuple:
        """
        Decide whether to inject a failure at this point in execution.

        Appends one or more dicts to self._pending_log as a side effect.
        Must never block or perform IO — hot path.

        Args:
            step_index:  Current step number in the trajectory (0-indexed).
            phase:       Current execution phase (planning/execution/synthesis).
            tool_name:   Name of the tool being dispatched, or None for timeout.
            draw_number: Global draw counter for this attempt (monotonic).
            attempt_id:  Current goal_attempt.attempt_id.
            goal_id:     Current goal_execution.goal_id.

        Returns:
            (should_inject: bool, failure_type_id: str | None)
            failure_type_id is None when should_inject is False.
        """
        ...

    @abstractmethod
    def get_algorithm_version(self) -> str:
        """
        Return the algorithm version string recorded in failure_injection_log.
        Bump this when injection logic changes so rows can be traced to code.
        """
        ...

    # ── Concrete interface (mirrors existing FailureInjector API) ─────────────
    # These methods allow ScenarioInjector to be a drop-in wherever
    # FailureInjector is currently used, without changing call sites.

    def is_active(self) -> bool:
        """Returns True when injection is enabled and allowed."""
        return self._enabled

    def set_exp_id(self, exp_id: int, total_draws: int = None) -> None:
        """
        Called after experiment row exists in DB.
        Subclasses may use exp_id and total_draws for slot pre-computation.
        Default: store exp_id only.
        """
        self._exp_id = exp_id

    def maybe_inject_timeout(self, rep_num: int, attempt_num: int) -> bool:
        """
        Timeout injection shim — called post-harness in goal_execution_manager.
        Routes to should_inject() with phase='post_harness', tool_name=None.
        draw_number uses attempt_num as a stable proxy.
        """
        if not self._enabled:
            return False
        inject, _ = self.should_inject(
            step_index=0,
            phase="post_harness",
            tool_name=None,
            draw_number=attempt_num,
            attempt_id=getattr(self, "_current_attempt_id", 0),
            goal_id=getattr(self, "_current_goal_id", 0),
        )
        return inject

    def maybe_inject_tool_failure(
        self,
        tool_name: str,
        rep_num: int,
        attempt_num: int,
    ) -> bool:
        """
        Tool failure injection shim — called inside _dispatch_tool() in agentic.py.
        Routes to should_inject() with phase='execution'.
        draw_number incremented externally by caller via _draw_counter attribute.
        """
        if not self._enabled:
            return False
        draw = getattr(self, "_draw_counter", 0) + 1
        self._draw_counter = draw
        inject, _ = self.should_inject(
            step_index=getattr(self, "_current_step", 0),
            phase="execution",
            tool_name=tool_name,
            draw_number=draw,
            attempt_id=getattr(self, "_current_attempt_id", 0),
            goal_id=getattr(self, "_current_goal_id", 0),
        )
        return inject

    def injection_summary(self) -> dict:
        """
        Return summary dict for injection_summary log at end of goal.
        Default implementation counts pending + flushed log entries.
        Subclasses should override for richer mode-specific summaries.
        """
        total = sum(
            1 for e in self._pending_log if e.get("status") == "injected"
        )
        return {
            "mode":           "scenario",
            "total_injected": total,
            "algorithm":      self.get_algorithm_version(),
        }

    def get_audit_log(self) -> list:
        """
        Return a copy of all buffered pending log entries.
        Does NOT clear the buffer — use get_pending_log() for that.
        Used for in-process inspection only (tests, dry-run reporting).
        """
        return list(self._pending_log)

    def get_pending_log(self) -> list:
        """
        Return buffered injection decisions and clear the buffer.

        Called by goal_execution_manager.py after finish_attempt().
        The returned list is batch-INSERTed to failure_injection_log.
        Each dict maps directly to failure_injection_log columns.

        Returns:
            List of dicts, one per injection decision since last flush.
            Empty list if no decisions were made (no injection active).
        """
        pending = list(self._pending_log)
        self._pending_log.clear()
        return pending
