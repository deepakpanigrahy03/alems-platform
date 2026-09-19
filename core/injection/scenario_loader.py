"""
================================================================================
scenario_loader.py — Injection Engine Factory
================================================================================

Single entry point for building injection engines from YAML config dicts.
Routes mode=scenario to ScenarioInjector.
Routes all other modes to the existing FailureInjector (backward compat).

Legacy conversion:
    Old flat YAML format (tool_failure_rate, timeout_rate) is detected and
    converted to scenario rule format internally before engine construction.
    The original config dict is never mutated — conversion produces a copy.

Scenario file loading:
    If scenario_file key is present in config, the external YAML file is
    loaded and its scenarios: list is merged into the config.
    Keeps experiment configs clean for complex multi-rule scenarios.

Usage:
    from core.injection.scenario_loader import build_injector
    injector = build_injector(fi_config, experiment_type)
    # injector is either ScenarioInjector or FailureInjector

Author: Deepak Panigrahy
SPEC: 8.6-A2, A2.3
================================================================================
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MODE_SCENARIO = "scenario"

# Base path for scenario files referenced by scenario_file: key.
# Relative paths are resolved from project root.
_SCENARIOS_BASE = Path("config/scenarios")


def build_injector(fi_config: dict, experiment_type: str):
    """
    Factory: build the right injection engine for the given config.

    Args:
        fi_config:        failure_injection section dict from YAML.
        experiment_type:  experiment_type string from study section.

    Returns:
        ScenarioInjector if mode=scenario.
        FailureInjector  for all other modes (backward compat).
        None             if enabled=False.
    """
    if not fi_config.get("enabled", False):
        return None

    mode = fi_config.get("mode", "statistical")

    if mode == MODE_SCENARIO:
        return _build_scenario_injector(fi_config, experiment_type)

    # All non-scenario modes: delegate to existing FailureInjector unchanged.
    # SC-5: existing class kept as-is, no modifications.
    try:
        from core.execution.failure_injector import FailureInjector
        injector = FailureInjector(fi_config, experiment_type)
        logger.info(
            "build_injector: FailureInjector mode=%s experiment_type=%s",
            mode, experiment_type,
        )
        return injector
    except Exception as exc:
        logger.warning("build_injector: FailureInjector construction failed: %s", exc)
        return None


def _build_scenario_injector(fi_config: dict, experiment_type: str):
    """
    Build ScenarioInjector from scenario-mode config.

    Handles:
        - scenario_file: key (load external YAML)
        - scenarios: key (inline rules)
        - legacy flat format (tool_failure_rate/timeout_rate conversion)
        - dry_run: key
        - scenario_id: key
    """
    from core.injection.scenario_injector import ScenarioInjector

    # Resolve scenario rules — three possible sources in priority order:
    # 1. scenario_file (external YAML)
    # 2. scenarios (inline list)
    # 3. legacy flat format (converted)

    scenarios = _resolve_scenarios(fi_config)
    if not scenarios:
        logger.warning(
            "_build_scenario_injector: no scenario rules found — injector disabled"
        )
        return None

    scenario_id = fi_config.get("scenario_id") or "__pending__"
    dry_run     = fi_config.get("dry_run", False)

    injector = ScenarioInjector(
        scenarios=scenarios,
        scenario_id=scenario_id,
        experiment_type=experiment_type,
        dry_run=dry_run,
    )
    logger.info(
        "_build_scenario_injector: ScenarioInjector built "
        "scenario_id=%r rules=%d dry_run=%s",
        scenario_id, len(scenarios), dry_run,
    )
    return injector


def _resolve_scenarios(fi_config: dict) -> list:
    """
    Resolve scenario rules from config, applying priority order.

    Returns list of rule dicts ready for ScenarioInjector.
    """
    # Priority 1: external scenario file
    scenario_file = fi_config.get("scenario_file")
    if scenario_file:
        return _load_scenario_file(scenario_file)

    # Priority 2: inline scenarios list
    scenarios = fi_config.get("scenarios")
    if scenarios:
        return list(scenarios)

    # Priority 3: legacy flat format conversion
    if "tool_failure_rate" in fi_config or "timeout_rate" in fi_config:
        logger.info(
            "_resolve_scenarios: legacy flat format detected — converting to scenario rules"
        )
        return _convert_legacy(fi_config)

    return []


def _load_scenario_file(scenario_file: str) -> list:
    """
    Load scenarios list from external YAML file.

    Resolves relative paths from _SCENARIOS_BASE.
    Returns empty list on any load error — never raises.
    """
    try:
        import yaml
        path = Path(scenario_file)
        if not path.is_absolute():
            path = _SCENARIOS_BASE / path
        with open(path) as f:
            data = yaml.safe_load(f)
        scenarios = data.get("scenarios", [])
        logger.info(
            "_load_scenario_file: loaded %d rules from %s",
            len(scenarios), path,
        )
        return scenarios
    except Exception as exc:
        logger.error(
            "_load_scenario_file: failed to load %r: %s", scenario_file, exc
        )
        return []


def _convert_legacy(fi_config: dict) -> list:
    """
    Convert old flat YAML format to scenario rules.

    tool_failure_rate → tool_error rule
    timeout_rate      → timeout rule

    The original config is not mutated.
    Converted rules use location={any, any, any} and unlimited max_injections
    to preserve existing behavior exactly.

    Returns list of scenario rule dicts.
    """
    rules = []

    tool_rate = float(fi_config.get("tool_failure_rate", 0.0))
    if tool_rate > 0.0:
        rules.append({
            "type":           "tool_error",
            "rate":           tool_rate,
            "location":       {"phase": "any", "step_index": "any", "tool_name": "any"},
            "max_injections": "unlimited",
            "_legacy_converted": True,
        })

    timeout_rate = float(fi_config.get("timeout_rate", 0.0))
    if timeout_rate > 0.0:
        rules.append({
            "type":           "timeout",
            "rate":           timeout_rate,
            "location":       {"phase": "any", "step_index": "any", "tool_name": "any"},
            "max_injections": "unlimited",
            "_legacy_converted": True,
        })

    logger.info(
        "_convert_legacy: converted %d rules (tool_rate=%.2f timeout_rate=%.2f)",
        len(rules), tool_rate, timeout_rate,
    )
    return rules
