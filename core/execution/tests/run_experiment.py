#!/usr/bin/env python3
"""
================================================================================
REAL EXPERIMENT – Run statistically significant experiments
================================================================================
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path


import numpy as np
from dotenv import load_dotenv
import logging
logger = logging.getLogger(__name__)
progress = logging.getLogger("alems.progress")  # 39.5.2e run progress
from core.observability.console import get_console
load_dotenv()

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from core.config_loader import ConfigLoader
from core.execution.agentic import AgenticExecutor
from core.execution.base import calc_stats
from core.execution.display_formatter import display_pair_hardware
from core.execution.experiment_runner import ExperimentRunner
from core.execution.harness import ExperimentHarness
from core.execution.linear import LinearExecutor
from core.utils.task_loader import list_task_summary, load_tasks
from core.utils.preflight import preflight
from core.execution.experiment_config_loader import apply_config
from core.execution.goal_execution_manager import execute_goal
from core.execution.goal_tracker import GoalTracker
from core.execution.retry_coordinator import RetryCoordinator, ExecutionResult

_goal_tracker = GoalTracker()       # stateless singleton
_retry_coordinator = RetryCoordinator()

def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Run A-LEMS experiments")
    parser.add_argument("--repetitions", "-n", type=int, default=None)
    parser.add_argument("--cool-down", type=int, default=None)
    parser.add_argument("--tasks", type=str, default="gsm8k_basic,factual_qa")
    parser.add_argument("--list-tasks", action="store_true")
    parser.add_argument("--no-warmup", action="store_true")
    parser.add_argument("--provider", type=str, default="llama_cpp",
        help="Provider from models.yaml e.g. groq, llama_cpp, ollama_remote")
    parser.add_argument("--country", type=str, default="US")
    parser.add_argument("--save-db", action="store_true")
    parser.add_argument(
        "--quality-enabled",
        action="store_true",
        default=False,
        help=(
            "Enable LLM judge scoring and output_quality persistence. "
            "Default false: retry, injection, and energy-only runs pay no "
            "LLM judge cost. Cheap local scorers (exact_match, numeric, "
            "structural, semantic) always run per task expectation block. "
            "Overrides quality.enabled in experiment config YAML when set."
        ),
    )
    parser.add_argument("--providers", type=str, help="Comma-separated providers")
    parser.add_argument(
        "--verbose", action="store_true", help="Show detailed hardware output per pair"
    )
    parser.add_argument(
        "--optimizer", action="store_true", help="Use optimizer wrapper"
    )
    parser.add_argument(
        '--experiment-type',
        default='normal',
        choices=[
            'normal','overhead_study','retry_study','failure_injection',
            'quality_sweep','calibration','ablation','pilot','debug',
        ],
        help='Research intent of this experiment run.',
    )
    parser.add_argument(
        '--experiment-goal',
        default=None,
        help='Human readable research question being answered.',
    )
    parser.add_argument(
        '--workflow-mode',
        default='comparison',
        choices=['linear', 'agentic', 'comparison'],
        help='Which workflow sides to run. comparison runs both.',
    )
    parser.add_argument(
        '--config',
        default=None,
        help='Path to experiment config YAML. Overrides individual CLI args.',
    )
    return parser.parse_args()


def run_provider_task(
    harness, runner, task, provider, repetitions, cool_down, args, config
):
    """
    Run all repetitions for ONE provider-task combination.
    Returns stats dict or None if failed.

    Args:
        harness: ExperimentHarness instance
        runner: ExperimentRunner instance
        task: Task dictionary
        provider: Provider name
        repetitions: Number of repetitions
        cool_down: Cool down seconds
        args: Command line arguments
        config: ConfigLoader instance
    """
    progress.info("%s | %s", provider, task["name"])

    # ========================================================================
    # Setup executors for this provider
    # ========================================================================
    models_for_provider = runner.config.list_models(provider)
    if not models_for_provider:
        logger.error("no models found for provider %s; skipping", provider)
        return []
    model_id = getattr(runner.args, 'model', None) or models_for_provider[0]["model_id"]
    linear_config  = runner.config.get_model_config_v2(provider, model_id)
    agentic_config = runner.config.get_model_config_v2(provider, model_id)

    if not linear_config or not agentic_config:
        logger.error("failed to load %s configs; skipping", provider)
        return None
    # ========================================================================
    # PRE-FLIGHT CHECKS - Validate before creating executors
    # ========================================================================
    from core.utils.preflight import preflight
    dummy = type('obj', (object,), {'config': linear_config})()
    preflight(dummy, provider)

    if args.optimizer:
        from core.execution.optimizer_wrapper import OptimizedExecutorWrapper

        linear = OptimizedExecutorWrapper(linear_config, "linear")
        agentic = OptimizedExecutorWrapper(agentic_config, "agentic")
    else:
        linear = LinearExecutor(linear_config)
        agentic = AgenticExecutor(agentic_config)

    progress.info("optimizer  %s", "yes" if args.optimizer else "no")

    # ========================================================================
    # Setup database for this provider-task
    # ========================================================================
    db = None
    exp_id = None
    hw_id = None
    runs_completed = 0

    try:
        if args.save_db:
            db, hw_id, env_id = runner.setup_database()

            config.sync_task_categories(db.db.conn)
            #runner.ensure_baseline_in_db(db, harness)
            exp_id = runner.create_experiment(
                db,
                task["id"],
                task["name"],
                provider,
                linear_config,
                args.country,
                repetitions,
                hw_id,
                env_id,
                optimizer=args.optimizer,
                experiment_type=getattr(args, 'experiment_type', 'normal'),
                experiment_goal=getattr(args, 'experiment_goal', None),
                workflow_mode=getattr(args, 'workflow_mode', 'comparison'),                
            )

        # Storage for results
        linear_results = []
        agentic_results = []
        taxes = []
        runs_completed = 0

        # ========================================================================
        # Repetition loop
        # ========================================================================
        try:
            for rep in range(repetitions):
                progress.info("repetition %d/%d", rep + 1, repetitions)
                from core.observability import status as _obs_status
                _obs_status.set_context(rep=rep + 1, total=repetitions)

                # Run linear
                workflow_mode = getattr(args, 'workflow_mode', 'comparison')

                # Normal path only — retry path skips harness here.
                # When max_retries > 0, execute_goal() owns the full lifecycle.
                linear_result = None
                agentic_result = None
                max_retries = getattr(args, "max_retries", 0)
                logger.debug("max_retries=%s workflow_mode=%s", max_retries, workflow_mode)
                if max_retries == 0:
                    if workflow_mode in ('linear', 'comparison'):
                        linear_result = harness.run_linear(
                            executor=linear,
                            prompt=task["prompt"],
                            task_id=task["id"],
                            is_cloud=not linear_config.get("is_local", False),
                            country_code=args.country,
                            run_number=rep + 1,
                        )
                        linear_results.append(linear_result)

                    if workflow_mode in ('agentic', 'comparison'):
                        agentic_result = harness.run_agentic(
                            executor=agentic,
                            task=task["prompt"],
                            task_id=task["id"],
                            is_cloud=not linear_config.get("is_local", False),
                            country_code=args.country,
                            run_number=rep + 1,
                            tool_graph=task.get("tool_graph"),
                        )
                        agentic_results.append(agentic_result)


                # Tax only meaningful when both sides ran
                if linear_result and agentic_result:
                    linear_energy  = linear_result["ml_features"]["energy_j"]
                    agentic_energy = agentic_result["ml_features"]["energy_j"]
                    tax = agentic_energy / linear_energy if linear_energy > 0 else 0
                    taxes.append(tax)
                    progress.info("pair  linear %.4f J  agentic %.4f J  tax %.2fx", linear_energy, agentic_energy, tax)

                # Save — pair or single depending on workflow_mode
                # Save — retry path when max_retries > 0, normal path otherwise.
                # Two paths intentionally separate — never merge them.
                if args.save_db and db:
                    max_retries = getattr(args, "max_retries", 0)
                    _has_injector = getattr(args, "failure_injector", None) is not None
                    if max_retries > 0 or _has_injector:
                        # Retry path — execute_goal() drives harness + persistence + goal tracking
                        policy = _retry_coordinator.load_policy(
                            db.db.conn, getattr(args, "policy_name", "default")
                        )
                        # Override DB policy with YAML retry flags if explicitly set.
                        # This lets experiment configs tune retry behavior without
                        # requiring DB migrations for new policy variants.
                        from dataclasses import replace as _dc_replace
                        _overrides = {}
                        if hasattr(args, "retry_on_timeout"):
                            _overrides["retry_on_timeout"] = args.retry_on_timeout
                        if hasattr(args, "retry_on_tool_error"):
                            _overrides["retry_on_tool_error"] = args.retry_on_tool_error
                        if hasattr(args, "retry_on_api_error"):
                            _overrides["retry_on_api_error"] = args.retry_on_api_error
                        if hasattr(args, "retry_on_wrong_answer"):
                            _overrides["retry_on_wrong_answer"] = args.retry_on_wrong_answer
                        if hasattr(args, "max_retries"):
                            _overrides["max_retries"] = args.max_retries
                        if hasattr(args, "backoff_seconds"):
                            _overrides["backoff_seconds"] = args.backoff_seconds
                        if _overrides:
                            policy = _dc_replace(policy, **_overrides)
                        # Full task first, so expectation, expected_answer and
                        # category reach scoring and persistence (G86); the keys
                        # execute_goal reads are then set explicitly as before.
                        task_dict = {
                            **task,
                            "id":     task.get("id"),
                            "name":   task.get("name"),
                            "prompt": task["prompt"],
                            "meta":   task.get("meta", {}),
                            "tool_graph": task.get("tool_graph"),
                        }
                        # A4: initialize retry adapter once per rep — args in scope here.
                        from core.retry.retry_adapter import get_retry_adapter
                        _retry_adapter = get_retry_adapter(args)
                        logger.debug("injector=%s adapter=%s", getattr(args, "failure_injector", None), type(_retry_adapter).__name__)
                        if workflow_mode in ("linear", "comparison"):
                            execute_goal(
                                db=db, exp_id=exp_id, hw_id=hw_id,
                                harness=harness, executor=linear,
                                task=task_dict, workflow_type="linear",
                                rep_num=rep + 1, goal_tracker=_goal_tracker,
                                policy=policy, failure_injector=getattr(args, "failure_injector", None),
                                repetitions=repetitions,
                                retry_adapter=_retry_adapter,
                                recovery_policy_id=getattr(args, "recovery_policy_strategy", "full_restart"),
                                cache_collector=getattr(args, "cache_collector", None),
                                quality_enabled=getattr(args, "quality_enabled", False),
                            )
                            runs_completed += 1
                        if workflow_mode in ("agentic", "comparison"):
                            execute_goal(
                                db=db, exp_id=exp_id, hw_id=hw_id,
                                harness=harness, executor=agentic,
                                task=task_dict, workflow_type="agentic",
                                rep_num=rep + 1, goal_tracker=_goal_tracker,
                                policy=policy, failure_injector=getattr(args, "failure_injector", None),
                                repetitions=repetitions,
                                retry_adapter=_retry_adapter,
                                recovery_policy_id=getattr(args, "recovery_policy_strategy", "full_restart"),
                                cache_collector=getattr(args, "cache_collector", None),
                                quality_enabled=getattr(args, "quality_enabled", False),
                            )
                            runs_completed += 1
                    else:
                        # Normal path — save_pair()/save_single() unchanged
                        if workflow_mode == 'comparison':
                            runner.save_pair(
                                db, exp_id, hw_id, linear_result, agentic_result, rep + 1,
                                task_id=task.get("id"), task_name=task.get("name"),
                                task_meta=task,
                            )
                            runs_completed = (rep + 1) * 2
                        elif workflow_mode == 'linear':
                            runner.save_single(
                                db, exp_id, hw_id, linear_result, rep + 1, 'linear',
                                task_meta=task,
                            )
                            runs_completed += 1
                        elif workflow_mode == 'agentic':
                            runner.save_single(
                                db, exp_id, hw_id, agentic_result, rep + 1, 'agentic',
                                task_meta=task,
                            )
                            runs_completed += 1
 
                    progress.info("repetition %d/%d saved", rep + 1, repetitions)
                    runner.update_progress(db, exp_id, runs_completed)

                # Cool down
                if rep < repetitions - 1:
                    progress.info("cool down %s s", cool_down)
                    from core.observability import status as _obs_status
                    _obs_status.wait(cool_down, "cool down")

        except (Exception, KeyboardInterrupt) as e:
            logger.error("%s/%s failed: %s", provider, task["name"], e, exc_info=True)

            if db and exp_id:
                error_msg = str(e) if str(e) else "KeyboardInterrupt (user cancelled)"
                runner.update_status(
                    db, exp_id, "failed", runs_completed, error=error_msg
                )
                print(
                    logger.warning("experiment %s marked failed after %s runs", exp_id, runs_completed)
                )

            return None

        # ========================================================================
        # UPDATE EXPERIMENT STATUS - SUCCESS
        # ========================================================================
        if db:
            final_runs = runs_completed if runs_completed > 0 else (
                len(linear_results) + len(agentic_results)
            )
            runner.update_status(db, exp_id, "completed", final_runs)
            progress.info("experiment %s completed with %s runs", exp_id, final_runs)

        # ========================================================================
        # DISPLAY HARDWARE PARAMETERS (verbose mode only)
        # ========================================================================
        if args.verbose:
            from core.execution.display_formatter import display_pair_hardware

            display_pair_hardware(
                linear_results,
                agentic_results,
                f"{provider} - {task['name']} HARDWARE DETAILS",
            )

        # ========================================================================
        # Calculate statistics for this provider-task
        # ========================================================================
        # When execute_goal path ran (max_retries>0 or injection active),
        # linear_results/agentic_results are empty — pull energy from DB.
        # energy_uj in goal_attempt is in microjoules — convert to joules.
        _used_execute_goal = getattr(args, "max_retries", 0) > 0 or \
                             getattr(args, "failure_injector", None) is not None
        if _used_execute_goal and exp_id and db:
            try:
                rows = db.db.conn.execute("""
                    SELECT ge.workflow_type,
                           SUM(ga.energy_uj) / 1e6  AS energy_j
                    FROM goal_execution ge
                    JOIN goal_attempt ga
                      ON ga.goal_id = ge.goal_id
                    WHERE ge.exp_id = ?
                      AND (ga.is_winning = 1
                           OR (ge.success = 0
                               AND ga.attempt_number = (
                                   SELECT MAX(ga2.attempt_number)
                                   FROM goal_attempt ga2
                                   WHERE ga2.goal_id = ge.goal_id
                               )))
                    GROUP BY ge.goal_id, ge.workflow_type
                """, (exp_id,)).fetchall()
                _lin, _agt = [], []
                for wf, ej in rows:
                    if wf == "linear":
                        _lin.append(ej or 0.0)
                    elif wf == "agentic":
                        _agt.append(ej or 0.0)
                if _lin:
                    linear_energies = _lin
                else:
                    linear_energies = [r["ml_features"]["energy_j"]
                                       for r in linear_results]
                if _agt:
                    agentic_energies = _agt
                else:
                    agentic_energies = [r["ml_features"]["energy_j"]
                                        for r in agentic_results]
                # Recompute taxes from DB values
                if _lin and _agt and len(_lin) == len(_agt):
                    taxes = [a / l if l > 0 else 0
                             for a, l in zip(_agt, _lin)]
            except Exception as _e:
                logger.warning("run_provider_task: DB energy fallback failed: %s", _e)
                linear_energies = [r["ml_features"]["energy_j"]
                                   for r in linear_results]
                agentic_energies = [r["ml_features"]["energy_j"]
                                    for r in agentic_results]
        else:
            linear_energies = [r["ml_features"]["energy_j"] for r in linear_results]
            agentic_energies = [r["ml_features"]["energy_j"] for r in agentic_results]

        stats = {
            "provider": provider,
            "task": task["name"],
            "linear_energy_j": calc_stats(linear_energies),
            "agentic_energy_j": calc_stats(agentic_energies),
            "orchestration_tax": calc_stats(taxes),
        }

        return stats

    except Exception as e:
        # ========================================================================
        # ERROR HANDLING - Update experiment status with error
        # ========================================================================
        logger.error("%s/%s failed: %s", provider, task["name"], e, exc_info=True)

        if db and exp_id:
            runner.update_status(db, exp_id, "failed", runs_completed, error=str(e))
            print(
                logger.warning("experiment %s marked failed after %s runs", exp_id, runs_completed)
            )

        return None

    finally:
        # ========================================================================
        # CLEANUP - Always close database
        # ========================================================================
        if db:
            db.close()


def run_task(harness, runner, task, providers, repetitions, cool_down, args, config):
    """
    Run one task across all providers.
    Returns list of stats for each provider.
    """
    results = []

    for provider in providers:
        stats = run_provider_task(
            harness, runner, task, provider, repetitions, cool_down, args, config
        )
        if stats:
            results.append(stats)
            progress.info(
                "%s | %s complete  tax %.2fx [%.2f, %.2f]", provider, task["name"],
                stats["orchestration_tax"]["mean"], stats["orchestration_tax"]["ci_lower"],
                stats["orchestration_tax"]["ci_upper"],
            )

    return results


def run_all_experiments(args):
    """
    Run all tasks with all providers.
    Returns list of all results.
    """
    progress.info("A-LEMS experiment")

    # Load configuration
    config = ConfigLoader()
    settings = config.get_settings()

    # Get defaults
    default_repetitions = (
        getattr(settings.experiment, "default_iterations", 30)
        if hasattr(settings, "experiment")
        else 30
    )
    default_cool_down = (
        getattr(settings.experiment, "cool_down_seconds", 30)
        if hasattr(settings, "experiment")
        else 30
    )

    repetitions = (
        args.repetitions if args.repetitions is not None else default_repetitions
    )
    cool_down = args.cool_down if args.cool_down is not None else default_cool_down

    # Parse providers
    if args.providers:
        providers = [p.strip() for p in args.providers.split(",")]
    elif args.provider:
        providers = [args.provider]
    else:
        providers = ["groq"]

    # Load tasks — prefer task_ids set by apply_config() from YAML,
    # fall back to --tasks CLI arg (comma-separated string)
    all_tasks = load_tasks()
    # CLI --tasks always wins over YAML task_ids — explicit override
    cli_tasks_provided = "--tasks" in sys.argv
    if cli_tasks_provided and args.tasks != "gsm8k_basic,factual_qa":
        # Non-default CLI --tasks specified — use it
        if args.tasks == "all":
            task_ids = [t["id"] for t in all_tasks]
        else:
            task_ids = [tid.strip() for tid in args.tasks.split(",")]
    elif hasattr(args, "task_ids") and args.task_ids:
        task_ids = args.task_ids
    elif args.tasks == "all":
        task_ids = [t["id"] for t in all_tasks]
    else:
        task_ids = [tid.strip() for tid in args.tasks.split(",")]
    selected_tasks = [t for t in all_tasks if t["id"] in task_ids]

    if not selected_tasks:
        logger.error("no valid tasks selected")
        return []

    progress.info(
        "configuration  providers %s  tasks %d  repetitions %s  cool down %ss",
        ", ".join(providers), len(selected_tasks), repetitions, cool_down,
    )

    # Create harness and runner
    harness = ExperimentHarness(config)
    # Run header: what runs and where records go (outside any window).
    from core.observability import run_report as _run_report
    _run_report.header(
        harness=harness, config=config, providers=providers, tasks=selected_tasks,
        repetitions=repetitions, cool_down=cool_down,
        country=getattr(args, "country", None), model=getattr(args, "model", None),
        profile=getattr(args, "profile", None) or getattr(args, "config", None),
    )
    runner = ExperimentRunner(config, args)

    # B3: build serving engine adapter and cache collector from YAML config.
    # serving_engine section optional — RemoteAPIAdapter used if absent (INV-7).
    import core.serving.bootstrap  # noqa: F401
    import core.telemetry.engine_backed_collector  # noqa: F401
    from core.serving.registry import ServingEngineRegistry
    from core.telemetry.engine_backed_collector import EngineBackedCollector
    _serving_cfg = getattr(args, "serving_engine", None)
    args.cache_collector = EngineBackedCollector(
        ServingEngineRegistry.from_config(_serving_cfg)
    )

    # Ensure baseline (once per session)
    baseline = runner.ensure_baseline(harness)
    harness.baseline = baseline

    # Run all experiments
    all_results = []

    inter_task_cooldown = getattr(args, "inter_task_cooldown", 0)

    for i, task in enumerate(selected_tasks):
        progress.info("task  %s (level %s)", task["name"], task["level"])

        task_results = run_task(
            harness, runner, task, providers, repetitions, cool_down, args, config
        )
        all_results.extend(task_results)

        # Inter-task cooldown — prevents rate limiting across tasks for cloud providers
        if inter_task_cooldown > 0 and i < len(selected_tasks) - 1:
            progress.info("inter task cool down %s s", inter_task_cooldown)
            time.sleep(inter_task_cooldown)

    return all_results, config, args


def display_master_summary(all_results):
    """Display final summary table."""
    if not all_results:
        return

    con = get_console()
    con.line("")
    con.line("=" * 85)
    con.line(con.style("MASTER SUMMARY", "bold"))
    con.line("=" * 85)
    con.line(con.style(
        f"{'Provider':<12} {'Task':<20} {'Linear (J)':>12} {'Agentic (J)':>12} {'Tax (x)':>10} {'CI Range':>18}",
        "bold"))
    con.line("-" * 85)

    for r in all_results:
        provider = r["provider"]
        task = r["task"][:18]
        linear = f"{r['linear_energy_j']['mean']:.4f}"
        agentic = f"{r['agentic_energy_j']['mean']:.4f}"
        tax_mean = r["orchestration_tax"]["mean"]
        ci_lower = r["orchestration_tax"]["ci_lower"]
        ci_upper = r["orchestration_tax"]["ci_upper"]

        if not np.isnan(ci_lower):
            ci_display = f"[{ci_lower:.2f}, {ci_upper:.2f}]"
            tax_display = f"{tax_mean:.2f}x"
        else:
            ci_display = "N/A"
            tax_display = f"{tax_mean:.2f}x*"

        # Pad before styling so the columns stay aligned on a terminal.
        con.line(
            f"{provider:<12} {task:<20} {linear:>12} {agentic:>12} "
            + con.style(f"{tax_display:>10}", "cyan") + " "
            + con.style(f"{ci_display:>18}", "dim" if str(ci_display).strip() == "N/A" else "")
        )

    con.line("=" * 85)


def main():
    """Main entry point - minimal logic."""
    args = parse_arguments()
    # 39.5.2a: run entry point; console plus per run memory buffer, no file I/O
    # during the run. Settings come from ALEMS_LOG_* (environment layer).
    from core.observability import setup_logging
    setup_logging("run")
    try:
        apply_config(args)
    except FileNotFoundError as e:
        print(f"\n  error: {e}", file=sys.stderr)
        print(f"  run: python run_experiment.py --help", file=sys.stderr)
        return 1
    if args.list_tasks:
        tasks = load_tasks()
        list_task_summary(tasks)
        return 0

    # A5: retry_policies list — run one experiment group per policy, then compare.
    retry_policies_list = getattr(args, "retry_policies", None)
    if retry_policies_list:
        from itertools import combinations
        from scripts.etl.policy_comparison_etl import compare_policies
        import sqlite3
        from scripts.tools.path_loader import get_alems_db_path
        all_group_ids = []
        for policy_entry in retry_policies_list:
            import copy
            policy_args = copy.copy(args)
            policy_args.retry_policies = None  # prevent re-entry
            policy_args.retry_engine = policy_entry.get("engine", "flat")
            policy_args.ear_policy_name = policy_entry.get("ear_policy_name", None)
            policy_args.policy_name = policy_entry.get("name", policy_entry.get("group_suffix", ""))
            suffix = policy_entry.get("group_suffix", policy_args.retry_engine)
            policy_args.group_suffix = suffix
            run_all_experiments(policy_args)
            # Fetch most recent group_id from DB for this policy run.
            try:
                _conn = sqlite3.connect(get_alems_db_path())
                _row = _conn.execute(
                    "SELECT group_id FROM experiments ORDER BY exp_id DESC LIMIT 1"
                ).fetchone()
                _conn.close()
                if _row:
                    all_group_ids.append((_row[0], policy_entry.get("name", suffix)))
            except Exception as _ge:
                logger.warning("Could not fetch group_id after policy run: %s", _ge)
        if len(all_group_ids) >= 2:
            conn = sqlite3.connect(get_alems_db_path())
            try:
                for (g_a, p_a), (g_b, p_b) in combinations(all_group_ids, 2):
                    try:
                        compare_policies(conn, g_a, g_b, p_a, p_b)
                    except Exception as _ce:
                        logger.warning("compare_policies failed %s vs %s: %s", g_a, g_b, _ce)
            finally:
                conn.close()
        return 0

    all_results, config, args = run_all_experiments(args)

    if all_results:
        pass  # master summary is shown last, after the run report (39.5.2e)

        # Save to JSON
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"data/experiment_{timestamp}.json"
        Path(filename).parent.mkdir(parents=True, exist_ok=True)

        with open(filename, "w") as f:
            json.dump(all_results, f, indent=2, default=str)
        progress.info("results saved  %s", filename)

    from core.observability import run_report as _run_report
    _run_report.footer()  # one card per run, read back from the store
    if all_results:
        display_master_summary(all_results)  # final view: the comparison table
    get_console().line(get_console().style("all experiments complete", "green"))
    # 39.5.2a: per run logs are written only here, after every measurement window.
    from core.observability import flush_run_log
    flush_run_log()
    return 0


if __name__ == "__main__":
    sys.exit(main())
