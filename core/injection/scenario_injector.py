"""
================================================================================
scenario_injector.py — ScenarioInjector
================================================================================

Implements InjectionEngine for mode=scenario YAML configs.
Supports per-type, per-location, seeded, max-capped injection.
Supports dry_run mode (evaluate rules, log decisions, never fire).

Decision lifecycle per should_inject() call:
    1. Iterate scenario rules in order.
    2. For each rule: check location match (phase, step_index, tool_name).
    3. If no match: log status='eligible', skip_reason='location_mismatch'.
    4. If match: Bernoulli draw using SHA-256-seeded RNG.
    5. If draw < rate AND max_injections not exceeded: status='injected'.
    6. If draw < rate BUT max_injections exceeded: status='suppressed'.
    7. If draw >= rate: status='skipped'.
    8. Append event dict to self._pending_log (no DB write here).

Realized trajectory evaluation (A2.7):
    Rules targeting steps beyond the actual trajectory get
    status='target_not_reached' logged at attempt completion.
    Call mark_trajectory_ended(final_step) after the attempt to trigger this.

Seed derivation (A2.6):
    seed = SHA256(scenario_id:run_number:rule_index:draw_number)[:16]
    Same seed + same scenario + same algorithm version = identical decisions.
    Deterministic across Python versions, platforms, process restarts.

Persistence:
    No DB writes inside this class. See injection_engine.py for contract.

Author: Deepak Panigrahy
SPEC: 8.6-A2, A2.4 through A2.9
================================================================================
"""

import hashlib
import logging
import time
from typing import Optional

from core.injection.injection_engine import InjectionEngine

logger = logging.getLogger(__name__)

ALGORITHM_VERSION = "v1"

INJECTION_ALLOWED_TYPES = frozenset({"failure_injection", "retry_study"})


def _compute_seed(
    scenario_id: str,
    run_number: int,
    rule_index: int,
    draw_number: int,
) -> tuple:
    """
    Derive deterministic float and hex seed from scenario context.

    Returns:
        (seed_float in [0,1), seed_hex str of length 16)
    seed_hex is stored in failure_injection_log.random_seed for audit.
    seed_float is compared against rule rate for Bernoulli draw.
    """
    raw = f"{scenario_id}:{run_number}:{rule_index}:{draw_number}"
    digest = hashlib.sha256(raw.encode()).digest()
    seed_float = int.from_bytes(digest[:8], "big") / (2 ** 64)
    seed_hex   = digest.hex()[:16]
    return seed_float, seed_hex


def _location_matches(rule_loc: dict, phase: str, step_index: int, tool_name: Optional[str]) -> bool:
    """
    Check whether a rule's location spec matches the current execution context.

    rule_loc keys: phase, step_index, tool_name.
    Value 'any' matches anything. step_index supports int or 'N-M' range string.

    Args:
        rule_loc:   location dict from scenario rule.
        phase:      current execution phase string.
        step_index: current step number (0-indexed).
        tool_name:  current tool name or None.

    Returns:
        True if all three location dimensions match.
    """
    # Phase check
    rule_phase = rule_loc.get("phase", "any")
    if rule_phase != "any" and rule_phase != phase:
        return False

    # step_index check — supports int, 'any', or 'N-M' range
    rule_step = rule_loc.get("step_index", "any")
    if rule_step != "any":
        if isinstance(rule_step, int):
            if step_index != rule_step:
                return False
        elif isinstance(rule_step, str) and "-" in str(rule_step):
            try:
                lo, hi = rule_step.split("-")
                if not (int(lo) <= step_index <= int(hi)):
                    return False
            except ValueError:
                logger.warning("_location_matches: bad step_index range=%r", rule_step)
                return False
        else:
            try:
                if step_index != int(rule_step):
                    return False
            except (ValueError, TypeError):
                logger.warning("_location_matches: unparseable step_index=%r", rule_step)
                return False

    # tool_name check
    rule_tool = rule_loc.get("tool_name", "any")
    if rule_tool != "any" and rule_tool != tool_name:
        return False

    return True


class ScenarioInjector(InjectionEngine):
    """
    Scenario-based failure injector.

    One instance per experiment goal (created fresh per goal by factory).
    Stateful: tracks injection counts per rule for max_injections enforcement.
    Thread-safety: not required — one injector per goal, sequential execution.
    """

    def __init__(
        self,
        scenarios: list,
        scenario_id: str,
        experiment_type: str,
        dry_run: bool = False,
    ) -> None:
        """
        Args:
            scenarios:        List of rule dicts from YAML scenarios: block.
            scenario_id:      Provenance key — stored in every log row.
            experiment_type:  Must be in INJECTION_ALLOWED_TYPES to activate.
            dry_run:          If True, evaluate rules and log but never fire.
        """
        super().__init__()
        self._scenarios      = scenarios
        self._scenario_id    = scenario_id
        self._dry_run        = dry_run
        self._exp_id         = 0
        self._run_number     = 0
        self._draw_counter   = 0

        # Per-rule injection count — keyed by rule_index.
        # Used for max_injections enforcement.
        self._rule_counts: dict = {}

        # Final step reached — set by mark_trajectory_ended().
        # Used to log target_not_reached for rules beyond actual trajectory.
        self._final_step: Optional[int] = None

        # Activate only when experiment_type is in allowed set.
        if experiment_type not in INJECTION_ALLOWED_TYPES:
            logger.warning(
                "ScenarioInjector: experiment_type=%r not in allowed set — disabled",
                experiment_type,
            )
            self._enabled = False
        else:
            self._enabled = True
            logger.info(
                "ScenarioInjector: active scenario_id=%r rules=%d dry_run=%s",
                scenario_id, len(scenarios), dry_run,
            )

    def get_algorithm_version(self) -> str:
        """Return algorithm version for failure_injection_log.injection_algorithm_version."""
        return ALGORITHM_VERSION

    def set_exp_id(self, exp_id: int, total_draws: int = None) -> None:
        """Store exp_id after experiment row created. No slot pre-computation needed."""
        self._exp_id = exp_id
        if self._scenario_id == "__pending__":
            # Fallback: scenario_id was not set at construction (edge case).
            self._scenario_id = str(exp_id)

    def mark_trajectory_ended(self, final_step: int, attempt_id: int, goal_id: int) -> None:
        """
        Called after attempt completes to log target_not_reached for rules
        that targeted steps beyond the actual trajectory.

        Args:
            final_step: Last step index reached before trajectory ended.
            attempt_id: Current attempt_id for log rows.
            goal_id:    Current goal_id for log rows.
        """
        self._final_step = final_step
        for rule_index, rule in enumerate(self._scenarios):
            loc = rule.get("location", {})
            rule_step = loc.get("step_index", "any")
            if rule_step == "any":
                continue
            try:
                target = int(rule_step)
            except (ValueError, TypeError):
                continue
            if target > final_step:
                self._pending_log.append({
                    "scenario_id":                 self._scenario_id,
                    "rule_index":                  rule_index,
                    "injected_type":               None,
                    "target_step":                 target,
                    "target_phase":                loc.get("phase", "any"),
                    "target_tool":                 loc.get("tool_name", "any"),
                    "injection_time_ns":           time.time_ns(),
                    "draw_number":                 0,
                    "random_seed":                 "",
                    "injection_algorithm_version": ALGORITHM_VERSION,
                    "status":                      "target_not_reached",
                    "skip_reason":                 f"execution_ended_at_step_{final_step}",
                    "attempt_id":                  attempt_id,
                    "goal_id":                     goal_id,
                    "run_id":                      None,
                })

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
        Evaluate all scenario rules against current execution context.

        Returns on first rule that fires (injected=True).
        All other rules are logged as eligible/skipped/suppressed.

        Returns:
            (True, failure_type_id) if a rule fires.
            (False, None) if no rule fires.
        """
        if not self._enabled:
            return False, None

        self._draw_counter += 1

        for rule_index, rule in enumerate(self._scenarios):
            failure_type = rule.get("type")
            rate         = float(rule.get("rate", 0.0))

            # Guard: reasoning-domain types cannot be injected.
            # They are measured via HallucinationDetector on real LLM runs.
            from core.injection.failure_simulator import NON_INJECTABLE_TYPES
            if failure_type in NON_INJECTABLE_TYPES:
                logger.error(
                    "ScenarioInjector: rule=%d type=%r is a reasoning-domain "
                    "failure and cannot be injected. "
                    "Measure it via HallucinationDetector on real LLM runs. "
                    "Remove from scenario YAML.",
                    rule_index, failure_type,
                )
                self._pending_log.append(self._make_log_row(
                    rule_index=rule_index,
                    failure_type=failure_type,
                    step_index=step_index,
                    phase=phase,
                    tool_name=tool_name,
                    draw_number=draw_number,
                    seed_hex="",
                    status="suppressed",
                    skip_reason="non_injectable_reasoning_domain_type",
                    attempt_id=attempt_id,
                    goal_id=goal_id,
                ))
                continue
            loc          = rule.get("location", {})
            max_inj      = rule.get("max_injections", "unlimited")

            # Location match check
            if not _location_matches(loc, phase, step_index, tool_name):
                self._pending_log.append(self._make_log_row(
                    rule_index=rule_index,
                    failure_type=None,
                    step_index=step_index,
                    phase=phase,
                    tool_name=tool_name,
                    draw_number=draw_number,
                    seed_hex="",
                    status="eligible",
                    skip_reason="location_mismatch",
                    attempt_id=attempt_id,
                    goal_id=goal_id,
                ))
                continue

            # Bernoulli draw — deterministic seed
            seed_float, seed_hex = _compute_seed(
                self._scenario_id, self._run_number, rule_index, draw_number
            )
            draw_passed = seed_float < rate

            if not draw_passed:
                self._pending_log.append(self._make_log_row(
                    rule_index=rule_index,
                    failure_type=None,
                    step_index=step_index,
                    phase=phase,
                    tool_name=tool_name,
                    draw_number=draw_number,
                    seed_hex=seed_hex,
                    status="skipped",
                    skip_reason=f"draw_{seed_float:.4f}_>=_rate_{rate:.4f}",
                    attempt_id=attempt_id,
                    goal_id=goal_id,
                ))
                continue

            # Draw passed — check max_injections
            current_count = self._rule_counts.get(rule_index, 0)
            if max_inj != "unlimited" and current_count >= int(max_inj):
                self._pending_log.append(self._make_log_row(
                    rule_index=rule_index,
                    failure_type=failure_type,
                    step_index=step_index,
                    phase=phase,
                    tool_name=tool_name,
                    draw_number=draw_number,
                    seed_hex=seed_hex,
                    status="suppressed",
                    skip_reason=f"max_injections_{max_inj}_reached",
                    attempt_id=attempt_id,
                    goal_id=goal_id,
                ))
                continue

            # Fire — unless dry_run
            status = "injected" if not self._dry_run else "selected"
            self._rule_counts[rule_index] = current_count + 1

            self._pending_log.append(self._make_log_row(
                rule_index=rule_index,
                failure_type=failure_type,
                step_index=step_index,
                phase=phase,
                tool_name=tool_name,
                draw_number=draw_number,
                seed_hex=seed_hex,
                status=status,
                skip_reason="dry_run_only" if self._dry_run else None,
                attempt_id=attempt_id,
                goal_id=goal_id,
            ))

            if self._dry_run:
                logger.info(
                    "ScenarioInjector: dry_run rule=%d type=%s step=%d phase=%s",
                    rule_index, failure_type, step_index, phase,
                )
                return False, None

            logger.info(
                "ScenarioInjector: INJECT rule=%d type=%s step=%d phase=%s tool=%r",
                rule_index, failure_type, step_index, phase, tool_name,
            )
            return True, failure_type

        return False, None

    def injection_summary(self) -> dict:
        """Mode-specific summary for end-of-goal logging."""
        total_injected = sum(self._rule_counts.values())
        return {
            "mode":           "scenario",
            "scenario_id":    self._scenario_id,
            "dry_run":        self._dry_run,
            "total_injected": total_injected,
            "by_rule":        dict(self._rule_counts),
            "algorithm":      ALGORITHM_VERSION,
            "min_met":        True,  # scenario mode has no global min_failures
        }

    def _make_log_row(
        self,
        rule_index: int,
        failure_type: Optional[str],
        step_index: int,
        phase: str,
        tool_name: Optional[str],
        draw_number: int,
        seed_hex: str,
        status: str,
        skip_reason: Optional[str],
        attempt_id: int,
        goal_id: int,
    ) -> dict:
        """
        Build one failure_injection_log row dict.
        Keys map directly to table columns — batch INSERT in execution manager.
        run_id is always None here — backfilled by ETL after runs row created.
        """
        return {
            "scenario_id":                 self._scenario_id,
            "rule_index":                  rule_index,
            "injected_type":               failure_type,
            "target_step":                 step_index,
            "target_phase":                phase,
            "target_tool":                 tool_name,
            "injection_time_ns":           time.time_ns(),
            "draw_number":                 draw_number,
            "random_seed":                 seed_hex,
            "injection_algorithm_version": ALGORITHM_VERSION,
            "status":                      status,
            "skip_reason":                 skip_reason,
            "attempt_id":                  attempt_id,
            "goal_id":                     goal_id,
            "run_id":                      None,
        }
