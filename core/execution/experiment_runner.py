#!/usr/bin/env python3
"""
================================================================================
EXPERIMENT RUNNER – Shared logic for all experiment scripts
================================================================================

This module contains ONLY the code that is duplicated between test_harness.py
and run_experiment.py. All original features remain in each script.

NEW FEATURES ADDED:
- Session grouping (group_id)
- Status tracking (running/completed/partial/failed)
- Multi-provider support
- Progress tracking (runs_completed/runs_total)

Author: Deepak Panigrahy
================================================================================
"""

import json
import platform
import os
import socket
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from core.utils.preflight import preflight


import psutil

# Add project root to path
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from core.config_loader import ConfigLoader
from core.database.manager import DatabaseManager
from core.models.baseline_measurement import BaselineMeasurement
from core.utils.provenance import record_run_provenance
from core.attribution.legacy_v1.phase_attribution_etl import compute_phase_attribution
from core.attribution.legacy_v1.aggregate_hardware_metrics import aggregate_hardware_metrics
# 16D3: ARM PMU cache data → cpu_samples row builder
# 16D4: Darwin PMU cache data → cpu_samples row builder (mirrors ARM pattern)
from core.execution.darwin_cpu_sample_builder import _build_darwin_cpu_sample_row
from core.execution.arm_cpu_sample_builder import _build_arm_cpu_sample_row
from core.execution.sample_processor import calculate_thermal_metrics
from core.attribution.legacy_v1.energy_attribution_etl import compute_energy_attribution
from core.attribution.legacy_v1.duration_fix_etl import fix_run, fix_run_with_pretask
from core.execution.run_persistence import RunPersistenceService
from core.attribution.legacy_v1.ttft_tpot_etl import populate_run as populate_ttft_tpot
from core.attribution.conservation_residual import compute_conservation_residual
from core.vocabularies.agent.span_builder import build_spans_from_result
from core.execution.goal_tracker import GoalTracker
import core.attribution.legacy_v1.goal_execution_etl as goal_execution_etl
import core.attribution.legacy_v1.energy_attribution_etl as energy_attribution_etl
from core.extensions.manager import ExtensionManager
from core.extensions.abc import PostRunPayload
from core.execution.hallucination_detector import HallucinationDetector
from core.execution.expectation.schema import Expectation
from core.execution.expectation.adapter import TaskExpectationAdapter
from core.execution import judgment_engine
from core.execution.judgment_types import JudgmentResult


def _span_hook(db, wconn, writer):
    """
    Return the after_run_row hook that flushes one SpanWriter for a run.

    Same two calls save_pair and save_single made right after insert_run.
    """
    def _hook(run_id):
        writer.flush_to_db(db, run_id)
        _backfill_attempt_span_id(wconn, run_id, writer)
    return _hook

# OutputQualityExtension loaded conditionally via entry point (39.5a).
# Direct import removed — extension must be declared in alems.extensions
# and quality.enabled=true in the experiment config to take effect.
# Falls back to None when not installed or not enabled.
def _load_output_quality_extension():
    # type: () -> object
    """
    Discover OutputQualityExtension via entry point.
    Returns the class instance if the extension is installed, else None.
    Failure to load is a warning not an error — the run proceeds without
    quality persistence. Consistent with ExtensionABC lifecycle contract.
    """
    try:
        from importlib.metadata import entry_points
        eps = entry_points(group="alems.extensions")
        for ep in eps:
            if ep.name == "output_quality":
                cls = ep.load()
                return cls()
        return None
    except Exception as _e:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "output_quality extension not loaded: %s", _e
        )
        return None
import core.execution.adapters.bootstrap  # noqa: F401
import core.serving.bootstrap  # noqa: F401 — registers RemoteAPIAdapter + VLLMAdapter
import core.telemetry.engine_backed_collector  # noqa: F401 — registers EngineBackedCollector
# Module-level singletons — stateless, safe to reuse across attempts.
# QualityJudge/quality_judge.py removed (SPEC 35J): confirmed zero live
# call sites this session — dead code, never actually invoked in
# production despite its docstring claiming otherwise. The real live
# scoring path was always TaskExpectationAdapter -> ScorerRegistry,
# with raw SQL hand-written below (also fixed by this change, see the
# score_result block further down).
_hallucination_detector = HallucinationDetector()
_expectation_adapter = TaskExpectationAdapter()
# Discovered via entry point at startup. None when not installed.
# persist() is called only when quality_enabled=True in experiment config.
_output_quality_extension = _load_output_quality_extension()
from core.execution.retry_coordinator import RetryCoordinator, ExecutionResult
from core.execution.failure_classifier import FailureClassifier
from core.execution.failure_injector import FailureInjector
from core.readers.power_rail_sampler import PowerRailSampler
from core.storage.inprocess_writer import InProcessWriter
from core.storage.resolver import resolve_store
import logging
logger = logging.getLogger(__name__)
 
# Extension manager singleton — initialized once at module import time.
# In legacy mode (no [extensions] in app_settings.yaml), this manager
# is a no-op and all existing direct-write code paths run unchanged.
_extension_manager = ExtensionManager()

_goal_tracker = GoalTracker()   # module-level singleton — stateless class
_failure_classifier = FailureClassifier()  # stateless — classify failures on normal path



def _insert_nic_samples(db, run_id: int, samples: list, conn=None) -> None:
    """Insert NIC byte counter samples to nic_samples table. Never raises (PAC-4)."""
    if not samples:
        return
    _conn = conn if conn is not None else db.db.conn
    try:
        rows = [
            (
                run_id,
                s.get("sample_ns"),
                s.get("interface"),
                s.get("tx_bytes"),
                s.get("rx_bytes"),
                s.get("tx_packets"),
                s.get("rx_packets"),
                s.get("sample_start_ns"),
                s.get("sample_end_ns"),
            )
            for s in samples
        ]
        _conn.executemany("""
            INSERT INTO nic_samples
                (run_id, sample_ns, interface, tx_bytes, rx_bytes,
                 tx_packets, rx_packets, sample_start_ns, sample_end_ns)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)
        _conn.commit()
        logger.debug("_insert_nic_samples: run=%d rows=%d", run_id, len(rows))
    except Exception as exc:
        logger.warning("_insert_nic_samples failed run=%d: %s", run_id, exc)

def _convert_gpu_to_telemetry(gpu_samples):
    """Convert GpuSample list to DeviceTelemetrySample list for device_telemetry table.
    PAC-2: wrapped at call site — this function does not raise.

    BUG-02 fix (2026-09-06): DeviceTelemetrySample requires timestamp_ns,
    interval_ns, and device_type (no defaults) — this call was missing
    all three, causing every device_telemetry insert to fail on agentic
    runs (caught and logged, never crashed the run, but the table was
    never populated). GpuSample already carries sample_end_ns and
    interval_ns directly — no upstream signature change needed, this
    constructor call just wasn't using what was already available.
    """
    from core.readers.energy_sample_v2 import DeviceTelemetrySample, SOURCE_DCGM, SOURCE_NVML
    result = []
    for s in gpu_samples:
        src = SOURCE_DCGM if getattr(s, "source", "") == "dcgm" else SOURCE_NVML
        result.append(DeviceTelemetrySample(
            timestamp_ns=s.sample_end_ns,
            interval_ns=s.interval_ns,
            device_type="GPU",
            source_id=src,
            power_mw=s.power_mw,
            util_pct=s.util_gpu_pct,
            temp_c=s.temperature_c,
            clock_mhz=s.sm_clock_mhz,
            mem_util_pct=s.util_mem_pct,
        ))
    return result

import platform
import pathlib

def _get_power_paths() -> dict:
    import json
    cfg_path = pathlib.Path(__file__).parent.parent.parent / "config" / "hw_config.json"
    try:
        cfg = json.loads(cfg_path.read_text())
        return cfg.get("spbm", {}).get("power_paths", {})
    except Exception as e:
        logger.warning("_get_power_paths: failed to read hw_config: %s", e)
        return {}


def _auto_expectation(expected_answer: str, task_meta: dict) -> dict:
    """
    Auto-detect scorer type from expected_answer string.

    Called when a task has expected_answer at top level but no explicit
    expectation: block. Covers 42 existing tasks without YAML changes.

    Rules:
        Contains a digit → numeric (math, calculations, ratios)
        5 words or fewer → exact (names, short facts)
        Otherwise        → semantic (prose descriptions, summaries)

    Args:
        expected_answer: The expected_answer string from tasks.yaml.
        task_meta      : Full task dict (unused currently, reserved for
                         future category-based overrides).

    Returns:
        expectation dict compatible with Expectation.from_dict().
    """
    import re
    text = expected_answer.strip()

    # Numeric: contains a number (integer, float, fraction, percentage).
    if re.search(r"\d", text.replace(",", "")):
        return {
            "scorer_type": "numeric",
            "expected_source": "inline",
            "answer": text,
        }

    # Short fixed string (5 words or fewer): exact match.
    if len(text.split()) <= 5:
        return {
            "scorer_type": "exact",
            "expected_source": "inline",
            "answer": text,
        }

    # Prose description: semantic similarity.
    return {
        "scorer_type": "semantic",
        "expected_source": "inline",
        "answer": text,
    }

# ---------------------------------------------------------------------------
# Extension post-run dispatch helper (35D)
# Module-level function so it can be called from both save_pair() and
# save_single() without a self reference. Lives here alongside the other
# module-level helpers (_insert_nic_samples, _convert_gpu_to_telemetry, etc.)
# ---------------------------------------------------------------------------

def _run_quality_scoring(db: object, goal_id: int, result: dict, workflow_type: str, conn=None, quality_enabled: bool = False) -> None:
    """
    Run quality scoring for all attempts in a completed goal.

    Uses TaskExpectationAdapter to resolve the task's expectation block
    and dispatch to the correct scorer via ScorerRegistry.
    Supports exact, numeric, semantic, rubric, and structural scoring.

    Cheap local scorers (exact_match, numeric, structural, semantic) always
    run when the task declares an expectation block. The LLM judge (rubric
    scorer), OutputQualityExtension.persist(), and hallucination detection
    are gated on quality_enabled=True. This is set from quality.enabled in
    the experiment config YAML. Default false: energy and retry studies pay
    no LLM judge cost.

    Args:
        db             : DatabaseInterface wrapper.
        goal_id        : goal_execution primary key.
        result         : Result dict from harness including task_meta.
        workflow_type  : "agentic" or "linear".
        conn           : Optional raw connection. Resolved from db if None.
        quality_enabled: When True, runs LLM judge and persists output_quality
                         rows and hallucination events. When False, cheap local
                         scorers still run but LLM judge is skipped.
    """
    try:
        if conn is None:
            conn = db.db.conn

        # Extract task metadata.
        task_meta = result.get("task_meta", {}) or {}
        task_category = task_meta.get("category") or result.get("task_category")
        task_id_value = task_meta.get("id") or result.get("task_id")

        # Resolve expectation — explicit block takes priority, then auto-detect
        # from expected_answer at top level. 42 existing tasks use expected_answer.
        expectation_dict = task_meta.get("expectation") or {}
        if not expectation_dict and task_meta.get("expected_answer"):
            expectation_dict = _auto_expectation(
                task_meta["expected_answer"], task_meta
            )
        expectation = Expectation.from_dict(expectation_dict)

        if not expectation.is_scoreable():
            # Task has no expectation and no expected_answer — skip silently.
            return
 
        # Get actual LLM response text.
        # Primary: result["execution"]["response"] (both linear and agentic).
        exec_block = result.get("execution", {}) or {}
        actual_output = (
            exec_block.get("response")
            or result.get("response")
            or result.get("output")
            or ""
        )
 
        # Find all attempts for this goal.
        attempts = conn.execute(
            "SELECT attempt_id, run_id, outcome FROM goal_attempt "
            "WHERE goal_id = ? ORDER BY attempt_id",
            (goal_id,),
        ).fetchall()
 

        for attempt_id, run_id, outcome in attempts:
            try:
                # Get energy for this run.
                row = conn.execute(
                    "SELECT total_energy_uj FROM runs WHERE run_id = ? LIMIT 1",
                    (run_id,),
                ).fetchone()
                energy_uj = int(row[0] or 0) if row else 0
            except Exception as _e:
                logger.warning("energy error: %s", _e)
                energy_uj = 0

 
            # Score via TaskExpectationAdapter.
            try:
                # SPEC 35J: judgment_engine.judge() wraps TaskExpectationAdapter
                # (the real live scorer path) with N-judge reconciliation from
                # task_quality_config.n_judges — previously configured but never
                # honored anywhere live (TaskExpectationAdapter.score() only
                # ever made one call). This is the first time n_judges takes
                # effect. Falls back to n_judges=1 if no config row exists,
                # matching today's real single-call behavior exactly.
                computation = judgment_engine.judge(
                    conn=conn,
                    task_category=task_category,
                    task_id_value=task_id_value,
                    expectation=expectation,
                    model_output=actual_output,
                    agentic_result=result if workflow_type == "agentic" else None,
                    energy_uj=energy_uj,
                )
                logger.debug("score: attempt=%d score=%s method=%s", attempt_id, computation.result.normalized_score, computation.result.score_method)
            except Exception as _se:
                logger.error("judgment_engine.judge raised for attempt_id=%d: %s", attempt_id, _se)
                continue

            pass_fail = computation.result.pass_fail

            # OutputQualityExtension.persist() writes to extension-owned tables
            # (output_quality, output_quality_judges). Gated on quality_enabled
            # so retry/injection/energy-only studies pay no LLM judge cost.
            # Cheap scorer results (normalized_score, pass_fail) are written
            # to goal_attempt below regardless of this gate.
            quality_id = None
            if quality_enabled and _output_quality_extension is not None:
                try:
                    quality_id = _output_quality_extension.persist(
                        conn=conn,
                        attempt_id=attempt_id,
                        goal_id=goal_id,
                        computation=computation,
                    )
                except Exception as _pe:
                    logger.error(
                        "OutputQualityExtension.persist failed for attempt_id=%d: %s",
                        attempt_id, _pe,
                    )

            # goal_attempt is core — core still owns this write directly,
            # unlike output_quality (extension-owned). Unchanged from before.
            conn.execute(
                "UPDATE goal_attempt SET normalized_score=?, pass_fail=? "
                "WHERE attempt_id=?",
                (computation.result.normalized_score, pass_fail, attempt_id),
            )
            conn.commit()

            # SPEC 35J Bug 3 fix: goal_output had zero rows, ever — designed
            # but never wired. Write it here, on the winning attempt only,
            # matching goal_tracker's own is_winning logic (outcome=='success').
            if outcome == "success":
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO goal_output "
                        "(goal_id, run_id, attempt_id, output_text, output_type, capture_method) "
                        "VALUES (?, ?, ?, ?, 'answer', 'forward')",
                        (goal_id, run_id, attempt_id, actual_output or ""),
                    )
                    conn.commit()
                except Exception as _ge:
                    logger.error("goal_output insert failed for attempt_id=%d: %s", attempt_id, _ge)

            # Hallucination detection requires a quality_id from persist().
            # Gated on quality_enabled for the same reason as persist().
            if quality_enabled and pass_fail == 0:
                judgment = computation.result
                judgment.quality_id = quality_id
                _hallucination_detector.detect(
                    conn=conn,
                    attempt_id=attempt_id,
                    goal_id=goal_id,
                    actual_output=actual_output or "",
                    expected_output=str(expectation.get_expected_value() or ""),
                    judgment=judgment,
                )
 
    except Exception as exc:

        logger.error(
            "_run_quality_scoring failed for goal_id=%d: %s",
            goal_id,
            exc,
        )
 

def _backfill_attempt_span_id(conn: object, run_id: int, writer: object) -> None:
    # type: (object, int, object) -> None
    """
    Backfill goal_attempt.span_id from the attempt span written by SpanWriter.
    Called after flush_to_db so span rows are committed.
    Never raises.
    """
    try:
        attempt_span = next(
            (r for r in writer._spans if r.kind == "attempt"), None
        )
        if attempt_span is None:
            return
        conn.execute(
            "UPDATE goal_attempt SET span_id = ? WHERE run_id = ? AND span_id IS NULL",
            (attempt_span.span_id, run_id),
        )
        conn.commit()
    except Exception as exc:
        logger.warning(
            "_backfill_attempt_span_id: run_id=%d skipped: %s", run_id, exc
        )


def _backfill_span_outcome(conn: object, run_id: int, outcome: str) -> None:
    # type: (object, int, str) -> None
    """
    Update outcome attribute on goal and attempt spans after _record_goal_pair.
    Called after spans are flushed so span_attributes rows exist.
    Never raises.
    """
    try:
        rows = conn.execute(
            "SELECT span_id FROM spans WHERE run_id=? AND kind IN ('goal','attempt')",
            (run_id,),
        ).fetchall()
        for (span_id,) in rows:
            conn.execute(
                """
                INSERT OR REPLACE INTO span_attributes (span_id, key, value_text, value_type)
                VALUES (?, 'outcome', ?, 'string')
                """,
                (span_id, outcome),
            )
        conn.commit()
    except Exception as exc:
        logger.warning("_backfill_span_outcome: run_id=%d skipped: %s", run_id, exc)


def _write_quality_annotations(db: object, conn: object, run_id: int) -> None:
    # type: (object, object, int) -> None
    """
    Write quality score as span_annotation on the attempt span.

    Reads normalized_score and pass_fail from goal_attempt after
    _run_quality_scoring commits. Uses goal_attempt.span_id to link
    to the attempt span. Never raises -- annotation is observability only.
    """
    try:
        rows = conn.execute(
            """
            SELECT ga.span_id, ga.normalized_score, ga.pass_fail,
                   ga.attempt_number, ga.is_retry
            FROM goal_attempt ga
            WHERE ga.run_id = ?
              AND ga.normalized_score IS NOT NULL
            """,
            (run_id,),
        ).fetchall()
        if not rows:
            return
        # If span_id not backfilled yet, look it up from spans table.
        rows_with_span = []
        for span_id, score, pass_fail, attempt_num, is_retry in rows:
            if span_id is None:
                row = conn.execute(
                    "SELECT span_id FROM spans WHERE run_id=? AND kind='attempt' LIMIT 1",
                    (run_id,),
                ).fetchone()
                span_id = row[0] if row else None
            if span_id:
                rows_with_span.append((span_id, score, pass_fail, attempt_num, is_retry))
        rows = rows_with_span
        if not rows:
            return
        annotations = []
        for span_id, score, pass_fail, attempt_num, is_retry in rows:
            annotations.append({
                "span_id": span_id,
                "annotation_type": "quality_score",
                "annotation_version": "1",
                "source": "quality_judge",
                "payload": {
                    "normalized_score": score,
                    "pass_fail": pass_fail,
                    "attempt_number": attempt_num,
                    "is_retry": bool(is_retry),
                },
            })
        with db.transaction():
            db.insert_span_annotations(annotations)
    except Exception as exc:
        logger.warning(
            "_write_quality_annotations: run_id=%d skipped: %s", run_id, exc
        )


def _dispatch_post_run(db: object, run_id: int, result: dict, workflow_type: str) -> None:
    """
    Build a PostRunPayload and dispatch to active extensions.

    Called by save_pair() and save_single() after core commits.
    In legacy mode this function is never called (guarded by
    _extension_manager.is_legacy_mode() at both call sites).

    Reads energy_uj, duration_ns, and status from the result dict
    produced by the harness. Falls back to DB query if not present.

    Args:
        db           : DatabaseInterface wrapper instance.
        run_id       : Committed run_id from insert_run().
        result       : Result dict from harness.run_*().
        workflow_type: "agentic" or "linear".
    """
    try:
        # Extract core measurement fields from result dict.
        # These are set by the harness after measurement completes.
        energy_uj = int(result.get("energy_uj") or 0)
        duration_ns = int(result.get("duration_ns") or 0)
        status = result.get("status", "completed")
        model_name = result.get("model_name", "")
        baseline_id = result.get("baseline_id", "")

        # Retrieve exp_id and hw_id from the run record if not in result.
        # These are always available because insert_run() already committed.
        exp_id = int(result.get("exp_id") or 0)
        hw_id = int(result.get("hw_id") or 0)
        if not exp_id or not hw_id:
            row = db.db.conn.execute(
                "SELECT exp_id, hw_id FROM runs WHERE run_id = ? LIMIT 1",
                (run_id,),
            ).fetchone()
            if row:
                exp_id = row[0]
                hw_id = row[1]

        payload = PostRunPayload(
            run_id=run_id,
            exp_id=exp_id,
            hw_id=hw_id,
            workflow_type=workflow_type,
            model_name=model_name,
            energy_uj=energy_uj,
            duration_ns=duration_ns,
            status=status,
            baseline_id=baseline_id,
            db=db,
        )

        _extension_manager.run_post_run(payload)

    except Exception as exc:
        # Extension dispatch must never crash save_pair/save_single.
        # The core run record is already committed and is safe.
        logger.error(
            "Extension post-run dispatch failed for run_id=%d: %s",
            run_id,
            exc,
        )
            
class ExperimentRunner:
    """Shared experiment logic - ONLY duplicate code + new features"""

    def __init__(self, config_loader, args):
        self.config = config_loader
        self.args = args
        self.settings = config_loader.get_settings()
        self.group_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    def validate_experiment(self, executor, provider):
        """Run pre-flight checks before experiment."""
        preflight(executor, provider)

        # B3: instantiate serving engine adapter from YAML config.
        # No serving_engine section = RemoteAPIAdapter (backward compat).
        from core.serving.registry import ServingEngineRegistry
        from core.telemetry.engine_backed_collector import EngineBackedCollector
        _serving_cfg = self.config.get("serving_engine", None)
        self._serving_adapter = ServingEngineRegistry.from_config(_serving_cfg)
        self._cache_collector = EngineBackedCollector(self._serving_adapter)


        
    # ========================================================================
    # DUPLICATE CODE 1: Hardware info collection (identical in both scripts)
    # ========================================================================

    def get_hardware_info(self) -> Dict[str, Any]:
        """Get hardware info - loads from hw_config.json and flattens it"""
        hw_config_path = Path("config/hw_config.json")
        if hw_config_path.exists():
            with open(hw_config_path) as f:
                data = json.load(f)

                # Flatten nested structures using ONLY data from JSON
                flat_data = {
                    "hardware_hash": data.get("hardware_hash"),
                    "hostname": data.get("metadata", {}).get("hostname"),
                    "cpu_model": data.get("cpu_model"),
                    "cpu_cores": data.get("cpu_cores"),
                    "cpu_threads": data.get("cpu", {}).get("logical_cores"),
                    "cpu_architecture": data.get("metadata", {}).get("machine"),
                    "cpu_vendor": data.get("cpu_details", {}).get(
                        "vendor"
                    ),  # If exists
                    "cpu_family": data.get("cpu_details", {}).get("family"),
                    "cpu_model_id": data.get("cpu_details", {}).get("model"),
                    "cpu_stepping": data.get("cpu_details", {}).get("stepping"),
                    "has_avx2": data.get("cpu_flags", {}).get("has_avx2"),
                    "has_avx512": data.get("cpu_flags", {}).get("has_avx512"),
                    "has_vmx": data.get("cpu_flags", {}).get("has_vmx"),
                    "gpu_model": data.get("gpu_model"),
                    "gpu_driver": data.get("gpu", {}).get("driver"),
                    "gpu_count": data.get("gpu", {}).get("count"),
                    "gpu_power_available": data.get("gpu", {}).get("power_available"),
                    "ram_gb": data.get("ram_gb"),
                    "kernel_version": data.get("metadata", {}).get("release"),
                    "microcode_version": data.get("cpu", {}).get(
                        "microcode"
                    ),  # If exists
                    "rapl_domains": str(data.get("rapl", {}).get("available_domains")),
                    "rapl_has_dram": data.get("rapl", {}).get("has_dram"),
                    "rapl_has_uncore": "uncore"
                    in data.get("rapl", {}).get("available_domains", []),
                    "system_manufacturer": data.get("system", {}).get("manufacturer"),
                    "system_product": data.get("system", {}).get("product"),
                    "system_type": data.get("system", {}).get("type"),
                    "virtualization_type": data.get("system", {}).get("virtualization"),
                    "detected_at": data.get("metadata", {}).get("detected_at"),
                    "os_name": data.get("metadata", {}).get("system"),
                }
                return flat_data

    def get_environment_info(self) -> Dict[str, Any]:
        """Get environment information for reproducibility tracking"""
        import hashlib
        import json
        import platform
        import subprocess

        # Get git info
        git_commit = None
        git_branch = None
        git_dirty = None
        try:
            git_commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip()[:16]
            git_branch = subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"], text=True
            ).strip()
            git_dirty = bool(
                subprocess.check_output(
                    ["git", "status", "--porcelain"], text=True
                ).strip()
            )
        except:
            pass

        # Get dependency versions
        numpy_version = None
        torch_version = None
        transformers_version = None
        try:
            import numpy

            numpy_version = numpy.__version__
        except:
            pass
        try:
            import torch

            torch_version = torch.__version__
        except:
            torch_version = None
        try:
            import transformers

            transformers_version = transformers.__version__
        except:
            transformers_version = None

        # Build environment info
        env_info = {
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "os_name": platform.system(),
            "os_version": platform.version(),
            "kernel_version": platform.release(),
            "git_commit": git_commit,
            "git_branch": git_branch,
            "git_dirty": git_dirty,
            "numpy_version": numpy_version,
            "torch_version": torch_version,
            "transformers_version": transformers_version,
            "llm_framework": None,  # From experiment config
            "framework_version": None,  # Can be filled from model config
        }

        # Load schema_version from environment.json
        try:
            with open(Path("config/environment.json")) as f:
                env_info["schema_version"] = json.load(f).get("schema_version", 0)
        except Exception:
            env_info["schema_version"] = 0
        # Generate env_hash
        hash_input = json.dumps(
            {
                "python_version": env_info["python_version"],
                "git_commit": env_info["git_commit"],
                "numpy_version": env_info["numpy_version"],
                "schema_version": env_info["schema_version"],
            },
            sort_keys=True,
        )
        env_info["env_hash"] = hashlib.sha256(hash_input.encode()).hexdigest()[:16]

        return env_info

    # ========================================================================
    # Baseline measurement (from test_harness, add to run_experiment)
    # ========================================================================
    def ensure_baseline(self, harness) -> Optional[BaselineMeasurement]:
        """Get baseline (measure if needed) and insert to DB once.

        Returns None immediately on platforms where energy_measurement != direct.
        Modeled and unavailable energy tiers have no idle power to subtract.
        """
        energy_tier = harness.energy_engine.config.get("energy_measurement", "unavailable")
        if energy_tier != "direct":
            logger.info(
                f"ensure_baseline: skipped — energy_measurement={energy_tier}"
            )
            harness.baseline = None
            return None

        baseline_config = self.settings.get("experiment", {}).get("baseline", {})
        force_remeasure = baseline_config.get("force_remeasure", False)
        # Resolve cache path via machine-aware 3-layer logic (BDC-4)
        from core.utils.idle_baseline import get_baseline_cache_path
        cache_file = get_baseline_cache_path()
 
        # Check if cache file exists
        cache_path = Path(cache_file)
        # Store scoped resolution (G27): the reusable baseline is the newest one
        # in this run's own store, never a file that other stores also write.
        stored = None if force_remeasure else harness.baseline_mgr.get_latest()
        cache_exists = stored is not None
        
        # Determine if we need to measure
        needs_measure = force_remeasure or not cache_exists
        
        if needs_measure:
            print("\n" + "=" * 70)
            print("📏 MEASURING IDLE POWER BASELINE")
            print("=" * 70)

            duration = baseline_config.get("duration_seconds", 10)
            samples = baseline_config.get("num_samples", 3)
            pre_wait = baseline_config.get("pre_wait_seconds", 5)

            print(f"   Duration: {duration}s × {samples} samples = {duration * samples}s total")
            print("   Please don't use mouse/keyboard during this time.\n")

            try:
                harness.baseline = harness.energy_engine.measure_idle_baseline(
                    duration_seconds=duration,
                    num_samples=samples,
                    pre_wait_seconds=pre_wait,
                    force_remeasure=force_remeasure,
                    measure_gpu=baseline_config.get("measure_gpu", True),
                )
                
                # Insert to DB (only once, when measured)
                if harness.baseline is not None:
                    harness.baseline_mgr.save(harness.baseline)

                print(f"\n   ✅ Baseline measured and saved!")
                print(f"      Baseline ID: {harness.baseline.baseline_id}")
                # canonical keys after v61: PACKAGE, CORE (not package-0, core)
                print(f"      Package idle power: {harness.baseline.package_power_w:.3f} W")
                print(f"      Core idle power:    {harness.baseline.core_power_w:.3f} W")

            except Exception as e:
                import traceback
                print(f"\n   ⚠️ Baseline measurement failed: {e}")
                traceback.print_exc()
                print("   Continuing without baseline")
                return None
        else:
            # Load from cache if not already in memory
            # Always the store's own baseline: a baseline already in memory may
            # have been loaded from a cache written by another store.
            if not harness.baseline or harness.baseline.baseline_id != stored.baseline_id:
                try:
                    harness.baseline = stored
                    print(f"\n📏 Loaded baseline from store: {harness.baseline.baseline_id}")
                except Exception as e:
                    print(f"\n⚠️ Failed to load baseline from cache: {e}")
                    return None
            else:
                print(f"\n📏 Using existing baseline: {harness.baseline.baseline_id}")
        
        return harness.baseline


    # ========================================================================
    # DUPLICATE CODE 3: Database setup (similar in both scripts)
    # ========================================================================
    def setup_database(self) -> Tuple[DatabaseManager, int]:
        """Database setup - similar in both scripts"""
        db_config = self.config.get_db_config()
        db = DatabaseManager(db_config)
        db.create_tables()
        hw_id = db.insert_hardware(self.get_hardware_info())
        env_id = self.setup_environment(db)
        return db, hw_id

    # ========================================================================
    # DUPLICATE CODE 4: Run data preparation (identical in both scripts)
    # ========================================================================
    def prepare_run_data(self, results, baseline_id=None) -> List[Dict]:
        """Extract run data from ml_dataset - identical in both scripts"""
        all_runs = []
        if "ml_dataset" in results:
            # Linear runs
            if "linear_runs" in results["ml_dataset"]:
                for rd in results["ml_dataset"]["linear_runs"]:
                    run_package = {
                        "ml_features": rd,
                        "sustainability": {
                            "carbon": {"grams": rd.get("carbon_g", 0)},
                            "water": {"milliliters": rd.get("water_ml", 0)},
                            "methane": {"grams": rd.get("methane_mg", 0)},
                        },
                        "baseline_id": baseline_id,
                        "harness_timestamp": datetime.now().isoformat(),
                    }
                    all_runs.append(run_package)
            # Agentic runs
            if "agentic_runs" in results["ml_dataset"]:
                for rd in results["ml_dataset"]["agentic_runs"]:
                    run_package = {
                        "ml_features": rd,
                        "sustainability": {
                            "carbon": {"grams": rd.get("carbon_g", 0)},
                            "water": {"milliliters": rd.get("water_ml", 0)},
                            "methane": {"grams": rd.get("methane_mg", 0)},
                        },
                        "baseline_id": baseline_id,
                        "harness_timestamp": datetime.now().isoformat(),
                    }
                    all_runs.append(run_package)
        return all_runs

    # ========================================================================
    # DUPLICATE CODE 5: Energy sample conversion (identical in both scripts)
    # ========================================================================
    def convert_energy_samples(self, results) -> List[Dict]:
        """Convert energy samples - identical in both scripts"""
        samples = []
        if "energy_samples" in results:
            for sample in results["energy_samples"]:
                if len(sample) == 2 and isinstance(sample[1], dict):
                    timestamp, energy_dict = sample
                    samples.append(
                        {
                            "timestamp_ns": int(timestamp * 1_000_000_000),
                            "pkg_energy_uj": energy_dict.get("PACKAGE", energy_dict.get("package-0", 0)),
                            "core_energy_uj": energy_dict.get("CORE", energy_dict.get("CPU_P", energy_dict.get("core", 0))),
                            "uncore_energy_uj": energy_dict.get("UNCORE", energy_dict.get("uncore", 0)),
                            "dram_energy_uj": 0,
                        }
                    )
        return samples

    def setup_database(
        self,
    ) -> Tuple[DatabaseManager, int, int]:  # ← Returns 3 values now
        """Setup database connection and return db, hw_id, env_id"""
        db = DatabaseManager(self.config.get_db_config())

        # Create tables if needed
        db.create_tables()

        # Get or create hardware record
        hw_id = db.insert_hardware(self.get_hardware_info())

        # Get or create environment record  ← ADD THIS
        env_id = self._get_or_create_environment(db)

        return db, hw_id, env_id  # ← Return both IDs

    def _get_or_create_environment(self, db) -> int:
        """Auto-detect if environment changed - insert if new, return existing if same"""
        env_info = self.get_environment_info()
        current_hash = env_info["env_hash"]

        # Check if this environment hash already exists
        result = db.db.execute(
            "SELECT env_id FROM environment_config WHERE env_hash = ?", (current_hash,)
        )
        if result:
            existing_id = result[0]["env_id"]
            print(
                f"   ✅ Using existing environment: {existing_id} (hash: {current_hash})"
            )
            return existing_id

        # New environment - insert it
        print(f"   📦 New environment detected (hash: {current_hash}) - inserting...")
        new_id = db.insert_environment_config(env_info)
        print(f"   ✅ Created new environment: {new_id}")
        return new_id

    def setup_environment(self, db) -> int:
        """Get or create environment record"""
        env_info = self.get_environment_info()
        return db.insert_environment_config(env_info)

    def resolve_task_prompt(self, task: dict) -> dict:
        """
        Resolve task prompt at runtime based on tier.
        Tier 1: load prompt and expected_answer from BenchmarkLoader using
                task.benchmark config — prompt field in YAML is null.
        Tier 2/3: use task.prompt directly — already set in YAML.
        Returns enriched task dict with prompt and expected_answer populated.
        Never modifies original task dict — returns a shallow copy.
        """
        task = dict(task)  # shallow copy — do not mutate caller's dict
 
        tier = task.get("tier", 3)
        if tier != 1:
            # Tier 2 and 3 prompts are static in tasks.yaml — no resolution needed
            return task
 
        benchmark = task.get("benchmark")
        if not benchmark:
            logger.warning(
                "Tier 1 task %s missing benchmark config — using prompt as-is",
                task.get("id"),
            )
            return task
 
        try:
            from core.execution.benchmark_loader import BenchmarkLoader
            loader = BenchmarkLoader()
            sample = loader.get_task_prompt(
                dataset=benchmark["dataset"],
                sample_id=benchmark["sample_id"],
            )
            task["prompt"] = sample["prompt"]
            task["expected_answer"] = sample["expected_answer"]
            task["difficulty"] = sample.get("difficulty", "medium")
        except Exception as exc:
            logger.warning(
                "BenchmarkLoader failed for task %s: %s — using null prompt",
                task.get("id"), exc,
            )
 
        return task
    # ========================================================================
    # NEW FEATURE 1: Create experiment with group_id and status
    # ========================================================================
    def create_experiment(
        self,
        db,
        task_id,
        task_name,
        provider,
        linear_config,
        country_code,
        repetitions,
        hw_id,
        env_id,
        optimizer=False,
        experiment_type='normal',     # research intent — DB trigger enforces valid values
        experiment_goal=None,         # human research question free text
        workflow_mode='comparison',   # 'linear'|'agentic'|'comparison'        
    ) -> int:
        """Create experiment with session tracking (NEW)"""
        experiment_meta = {
            "name": f"{task_id}_{provider}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "description": f"Task: {task_name}",
            "workflow_type": "comparison",
            "model_name": linear_config.get("name", "unknown"),
            "provider": provider,
            "model_id":                 linear_config.get("model_id"),
            "execution_site":           linear_config.get("execution_site"),
            "transport":                linear_config.get("transport"),
            "remote_energy_available":  int(linear_config.get("remote_energy_available", False)),            
            "task_name": task_id,
            "country_code": country_code,
            "group_id": self.group_id,  # NEW
            "status": "running",  # NEW
            "started_at": datetime.now().isoformat(),  # NEW
            "runs_total": repetitions if workflow_mode in ("linear", "agentic") else repetitions * 2,  # linear + agentic
            "optimization_enabled": 1 if optimizer else 0,
            "hw_id": hw_id,
            "env_id": env_id,
            'experiment_type': experiment_type,    
            'experiment_goal': experiment_goal,    
            'workflow_type':   workflow_mode,            
        }
        return db.insert_experiment(experiment_meta)

    # ========================================================================
    # NEW FEATURE 2: Update experiment status
    # ========================================================================
    def update_status(
        self,
        db,
        exp_id: int,
        status: str,
        runs_completed: int = None,
        error: str = None,
    ):
        """Update experiment status (NEW)"""
        updates = {"status": status}
        if status in ["completed", "failed", "partial"]:
            updates["completed_at"] = datetime.now().isoformat()
        if runs_completed is not None:
            updates["runs_completed"] = runs_completed
        if error:
            updates["error_message"] = error

        set_clause = ", ".join([f"{k}=?" for k in updates.keys()])
        values = list(updates.values()) + [exp_id]
        db.db.execute(f"UPDATE experiments SET {set_clause} WHERE exp_id=?", values)

    def update_progress(self, db, exp_id: int, runs_completed: int):
        """
        Update progress of an experiment without changing status.

        Args:
            db: Database connection
            exp_id: Experiment ID
            runs_completed: Number of runs completed so far
        """
        db.db.execute(
            "UPDATE experiments SET runs_completed = ? WHERE exp_id = ?",
            (runs_completed, exp_id),
        )
        print(
            f"   📊 Progress: {runs_completed}/{self._get_total_runs(db, exp_id)} runs"
        )

    def _validate_run(self, db, run_id: int, hw_id) -> None:
        """Score a completed run and insert into run_quality. Called after each INSERT."""
        from core.utils.quality_scorer import QualityScorer
        run = db.get_run(run_id)
        hw_rows = db.db.execute(
            "SELECT hardware_hash FROM hardware_config WHERE hw_id = ?", (hw_id,)
        )
        scorer = QualityScorer()
        hardware_hash = hw_rows[0]["hardware_hash"] if hw_rows else "default"
        valid, score, reason = scorer.compute(run or {}, hardware_hash)
        db.db.execute(
            """INSERT OR REPLACE INTO run_quality
               (run_id, experiment_valid, quality_score, rejection_reason, quality_version)
               VALUES (?, ?, ?, ?, ?)""",
            (run_id, valid, score, reason, scorer.VERSION),
        )
    def _get_total_runs(self, db, exp_id: int) -> int:
        """Get total runs expected for experiment"""
        result = db.db.execute(
            "SELECT runs_total FROM experiments WHERE exp_id = ?", (exp_id,)
        )
        return result[0]["runs_total"] if result else 0

    # ========================================================================
    # NEW FEATURE 3: Multi-provider helper
    # ========================================================================
    def get_providers(self):
        """Get list of providers from args (NEW)"""
        if hasattr(self.args, "providers") and self.args.providers:
            return [p.strip() for p in self.args.providers.split(",")]
        elif hasattr(self.args, "provider") and self.args.provider:
            return [self.args.provider]
        else:
            return ["cloud"]

    # ========================================================================
    # CPU SAMPLES - Identical in both scripts
    # ========================================================================
    def get_cpu_samples(self, results) -> List[Dict]:
        """Get CPU samples - identical in test_harness and run_experiment"""
        samples = []
        if "cpu_samples" in results:
            samples = results["cpu_samples"]
            print(f"   Found {len(samples)} CPU samples (ready for insertion)")
            if samples and len(samples) > 0:
                print(f"   🔍 First CPU sample keys: {list(samples[0].keys())}")
                print(f"   🔍 First CPU sample values: {samples[0]}")
        return samples

    # ========================================================================
    # INTERRUPT SAMPLES - Identical in both scripts
    # ========================================================================
    def get_interrupt_samples(self, results) -> List[Dict]:
        """Get interrupt samples - identical in test_harness and run_experiment"""
        samples = []
        if "interrupt_samples" in results:
            samples = results["interrupt_samples"]
            print(f"   Found {len(samples)} interrupt samples (ready for insertion)")
        return samples

    def ensure_baseline_in_db(self, db, harness):
        """Save baseline to database once per experiment session."""
        if not harness.baseline:
            print("⚠️ No baseline available - foreign key constraints may fail")
            return False

        if hasattr(self, "_baseline_saved"):
            return True

        # Check if already in database
        try:
            # db is a DatabaseManager; the query adapter is db.db and returns a
            # list of dicts (G34, same defect class as G32).
            result = db.db.execute(
                "SELECT 1 FROM idle_baselines WHERE baseline_id = ?",
                (harness.baseline.baseline_id,),
            )
            if result:
                print(
                    f"✅ Baseline {harness.baseline.baseline_id} already exists in database"
                )
                self._baseline_saved = True
                return True
        except Exception as e:
            # Visible, never swallowed (DC-3): the insert below is still safe
            # because it uses INSERT OR IGNORE.
            logger.error("Baseline existence check failed: %s: %s", type(e).__name__, e)

        # Save baseline
        try:
            b = harness.baseline
            metadata = b.metadata or {}

            baseline_dict = {
                "baseline_id": b.baseline_id,
                "timestamp": b.timestamp,
                # canonical keys after v61: PACKAGE, CORE, UNCORE, DRAM
                "package_power_watts": b.power_watts.get("PACKAGE", 0),
                "core_power_watts":    b.power_watts.get("CORE",
                                       b.power_watts.get("CPU_P", 0)),
                "uncore_power_watts":  b.power_watts.get("UNCORE", 0),
                "dram_power_watts":    b.power_watts.get("DRAM", 0),
                "duration_seconds": b.duration_seconds,
                "sample_count": b.sample_count,
                "package_std": b.std_dev_watts.get("PACKAGE", b.std_dev_watts.get("package-0")),
                "core_std":    b.std_dev_watts.get("CORE", b.std_dev_watts.get("CPU_P", b.std_dev_watts.get("core"))),
                "uncore_std":  b.std_dev_watts.get("UNCORE", b.std_dev_watts.get("uncore")),
                "dram_std":    b.std_dev_watts.get("DRAM", b.std_dev_watts.get("dram")),
                "governor": metadata.get("governor"),
                "turbo": metadata.get("turbo"),
                "background_cpu": metadata.get("background_cpu"),
                "process_count": metadata.get("process_count"),
                "method": b.method,
            }

            db.insert_baseline(baseline_dict)
            self._baseline_saved = True
            print(f"✅ Baseline {b.baseline_id} saved to database")
            return True

        except Exception as e:
            print(f"❌ Failed to save baseline: {e}")
            return False

    def save_pair(self, db, exp_id, hw_id, linear_result, agentic_result, rep_num,
                  task_id=None, task_name=None, task_meta=None, writer=None):
        """Save one pair of runs with all samples."""
        _wconn = db.db.conn

        # Set run_number
        linear_result["ml_features"]["run_number"] = rep_num
        agentic_result["ml_features"]["run_number"] = rep_num
        # Derive task context from results if not passed explicitly by caller
        task_id   = task_id   or linear_result.get("task_id", "unknown")
        task_name = task_name or linear_result.get("task_name", task_id)
        task_meta = task_meta or linear_result.get("task_meta", {}) or {}
        # Inject task_meta into both result dicts so _run_quality_scoring can read it.
        linear_result["task_meta"] = task_meta
        agentic_result["task_meta"] = task_meta
        linear_outcome  = "success" if linear_result.get("execution", {}).get("status") == "success"  else "failure"
        agentic_exec = agentic_result.get("execution", {})
        agentic_outcome = "success" if (agentic_exec.get("status") == "success" or agentic_exec.get("execution", {}).get("status") == "success") else "failure"

        linear_copy = linear_result.copy()
        agentic_copy = agentic_result.copy()
        linear_copy["baseline_id"] = linear_copy["ml_features"].get("baseline_id")
        agentic_copy["baseline_id"] = agentic_copy["ml_features"].get("baseline_id")

        # Two SpanWriters sharing one trace_id — one per run_id (EEI-4).
        # trace_id groups linear and agentic under one experiment trace.
        from core.vocabularies.agent.span_writer import SpanWriter
        import uuid
        _trace_id = uuid.uuid4().hex
        _linear_writer = SpanWriter(trace_id=_trace_id)
        _linear_span_id = _linear_writer.open_span("run", f"linear:{task_id}")
        _linear_writer.close_span(_linear_span_id)
        _agentic_writer = SpanWriter(trace_id=_trace_id)
        _agentic_span_id = _agentic_writer.open_span("run", f"agentic:{task_id}")
        _agentic_writer.close_span(_agentic_span_id)
        # Build child spans from result data -- must happen before flush_to_db (EEI-4).
        _hw_info_spans = self.get_hardware_info()
        build_spans_from_result(_linear_writer, _linear_span_id, linear_result, "linear", _hw_info_spans)
        build_spans_from_result(_agentic_writer, _agentic_span_id, agentic_result, "agentic", _hw_info_spans)

        # All persistence for both runs through the one writer (C3, G77).
        # Each run commits on its own; spans flush right after each run row.
        _persist = RunPersistenceService()
        # C-EV stage recorders, one per run (39.5.2c). Rows are written after
        # both runs, also when persistence raises; the exception propagates.
        from core.observability.stages import StageRecorder, persist_after_run
        _lin_st = StageRecorder("save_pair")
        _agt_st = StageRecorder("save_pair")
        try:
            linear_id = _persist.insert_one_run(
                db, exp_id, hw_id, linear_result, "linear", rep_num,
                after_run_row=_span_hook(db, _wconn, _linear_writer),
                stages=_lin_st,
            )
            _lin_st.attach_run_id(linear_id)
            agentic_id = _persist.insert_one_run(
                db, exp_id, hw_id, agentic_result, "agentic", rep_num,
                after_run_row=_span_hook(db, _wconn, _agentic_writer),
                stages=_agt_st,
            )
            _agt_st.attach_run_id(agentic_id)
        finally:
            persist_after_run(_lin_st, db)
            persist_after_run(_agt_st, db)

        with db.transaction():
 
            # --- Extension post-run dispatch (35D) ---
            # Selective mode: call active extensions after core commits.
            # Legacy mode: _extension_manager.run_post_run() is a no-op;
            # all existing direct writes above continue to execute unchanged.
            if not _extension_manager.is_legacy_mode():
                _dispatch_post_run(db, agentic_id, agentic_result, "agentic")
                _dispatch_post_run(db, linear_id, linear_result, "linear")


            # Tax summary for this pair
            # Use attributed_energy_uj (L2: cpu_fraction x dynamic) — paper unit.
            # layer3_derived["workload"] = dynamic_energy_uj — includes background.
            # E4: one rule for all three paths (run_persistence); NULL, never a substitute.
            from core.execution.run_persistence import (
                attributed_energy_or_none, orchestration_energy_or_none)
            linear_uj  = attributed_energy_or_none(linear_result)
            agentic_uj = attributed_energy_or_none(agentic_result)

            print(
                f"🔍 DEBUG - linear_orchestration_uj from ml_features: {linear_result['ml_features'].get('orchestration_tax_uj')}"
            )
            print(
                f"🔍 DEBUG - agentic_orchestration_uj from ml_features: {agentic_result['ml_features'].get('orchestration_tax_uj')}"
            )

            linear_orchestration_uj  = orchestration_energy_or_none(linear_result)
            agentic_orchestration_uj = orchestration_energy_or_none(agentic_result)
            # GPU PP1 energy per workflow side — None on non-Tiger-Lake
            linear_gpu_uj  = linear_result["ml_features"].get("gpu_dynamic_energy_uj")
            agentic_gpu_uj = agentic_result["ml_features"].get("gpu_dynamic_energy_uj")            
            print(
                f"🔍 DEBUG - linear energy_uj keys: {linear_result['layer3_derived']['energy_uj'].keys()}"
            )
            print(
                f"🔍 DEBUG - agentic energy_uj keys: {agentic_result['layer3_derived']['energy_uj'].keys()}"
            )
            print(
                f"🔍 DEBUG - linear energy_uj content: {linear_result['layer3_derived']['energy_uj']}"
            )
            print(
                f"🔍 DEBUG - linear energy_uj content: {agentic_result['layer3_derived']['energy_uj']}"
            )

            db.create_tax_summary_for_pair(
                linear_id,
                agentic_id,
                linear_uj,
                agentic_uj,
                linear_orchestration_uj,
                agentic_orchestration_uj,
            )

        print(
            f"   ✅ Pair {rep_num} saved (linear: {linear_id}, agentic: {agentic_id})"
        )


     
        # ── Goal tracking wiring (8.5-A) ─────────────────────────────────────
        # One goal_execution + goal_attempt row per workflow side.
        # ETL populates energy rollup columns synchronously after both goals recorded.
        # Backfill outcome on goal and attempt spans now that goal tracking is done.
        _backfill_span_outcome(_wconn, linear_id, linear_outcome)
        _backfill_span_outcome(_wconn, agentic_id, agentic_outcome)
        linear_goal_id = self._record_goal_pair(
            db=db,
            exp_id=exp_id,
            task_id=task_id,
            task_name=task_name,
            task_meta=task_meta,
            workflow_type='linear',
            run_id=linear_id,
            outcome=linear_outcome,
            energy_uj=linear_uj,
            orchestration_uj=linear_orchestration_uj,
            compute_uj=None,
            gpu_energy_uj=linear_gpu_uj,
            conn=_wconn,
        )
        agentic_goal_id = self._record_goal_pair(
            db=db,
            exp_id=exp_id,
            task_id=task_id,
            task_name=task_name,
            task_meta=task_meta,
            workflow_type='agentic',
            run_id=agentic_id,
            outcome=agentic_outcome,
            energy_uj=agentic_uj,
            orchestration_uj=agentic_orchestration_uj,
            compute_uj=None,
            gpu_energy_uj=agentic_gpu_uj,
            conn=_wconn,
        )
        # Bug 7 fix: backfill attempt_id on orchestration_events for comparison path.
        # Also backfill started_at_ns/finished_at_ns from run timestamps — _record_goal_pair
        # creates attempt post-run so started_at_ns would reflect insert time, not run start.
        if agentic_goal_id is not None:
            _agentic_attempt = _wconn.execute(
                "SELECT attempt_id FROM goal_attempt WHERE run_id = ? LIMIT 1",
                (agentic_id,)
            ).fetchone()
            if _agentic_attempt:
                _agentic_run_ts = _wconn.execute(
                    "SELECT start_time_ns, end_time_ns FROM runs WHERE run_id = ? LIMIT 1",
                    (agentic_id,)
                ).fetchone()
                _wconn.execute(
                    "UPDATE orchestration_events SET attempt_id = ? WHERE run_id = ? AND attempt_id IS NULL",
                    (_agentic_attempt[0], agentic_id)
                )
                if _agentic_run_ts:
                    _wconn.execute(
                        "UPDATE goal_attempt SET started_at_ns = ?, finished_at_ns = ? WHERE attempt_id = ?",
                        (_agentic_run_ts[0], _agentic_run_ts[1], _agentic_attempt[0])
                    )
                _wconn.commit()
        # G142: the linear attempt needs the same backfill as agentic above; it
        # was created post run, so its times were bookkeeping times, not the
        # measured window (found by INV-A2).
        if linear_goal_id is not None:
            _linear_attempt = _wconn.execute(
                "SELECT attempt_id FROM goal_attempt WHERE run_id = ? LIMIT 1",
                (linear_id,)
            ).fetchone()
            if _linear_attempt:
                _linear_run_ts = _wconn.execute(
                    "SELECT start_time_ns, end_time_ns FROM runs WHERE run_id = ? LIMIT 1",
                    (linear_id,)
                ).fetchone()
                _wconn.execute(
                    "UPDATE orchestration_events SET attempt_id = ? WHERE run_id = ? AND attempt_id IS NULL",
                    (_linear_attempt[0], linear_id)
                )
                if _linear_run_ts:
                    _wconn.execute(
                        "UPDATE goal_attempt SET started_at_ns = ?, finished_at_ns = ? WHERE attempt_id = ?",
                        (_linear_run_ts[0], _linear_run_ts[1], _linear_attempt[0])
                    )
                _wconn.commit()
        # ETL runs sync — after both goals recorded so normalization_factors
        # sees the full picture for this experiment repetition.
        if linear_goal_id is not None:
            goal_execution_etl.process_one(linear_goal_id, _wconn)
            _goal_tracker.queue_etl(
                _wconn, 'goal_execution', linear_goal_id, 'goal_execution_etl',
            )
        if agentic_goal_id is not None:
            goal_execution_etl.process_one(agentic_goal_id, _wconn)
            _goal_tracker.queue_etl(
                _wconn, 'goal_execution', agentic_goal_id, 'goal_execution_etl',
            )
 

        # --- Quality scoring (8.5C) ---
        # Called AFTER goal_execution ETL so attempt_ids are committed.
        # Quality judge runs after core energy_uj is committed (Observer Energy).
        _qe = getattr(self.args, "quality_enabled", False) if hasattr(self, "args") else False
        if linear_goal_id is not None:
            _run_quality_scoring(db=db, goal_id=linear_goal_id, result=linear_result,
                                 workflow_type="linear", conn=_wconn, quality_enabled=_qe)
        if agentic_goal_id is not None:
            _run_quality_scoring(db=db, goal_id=agentic_goal_id, result=agentic_result,
                                 workflow_type="agentic", conn=_wconn, quality_enabled=_qe)
        # Quality annotations on attempt spans.
        _write_quality_annotations(db, _wconn, linear_id)
        _write_quality_annotations(db, _wconn, agentic_id)

        # Attribution stubs — runs sync after goal rows exist
        energy_attribution_etl.populate_attribution_stubs(linear_id, _wconn)
        energy_attribution_etl.populate_attribution_stubs(agentic_id, _wconn)
        _goal_tracker.queue_etl(_wconn, 'run', linear_id, 'energy_attribution_etl')
        _goal_tracker.queue_etl(_wconn, 'run', agentic_id, 'energy_attribution_etl')

        return linear_id, agentic_id
    def _record_goal_pair(
        self,
        db,
        exp_id: int,
        task_id: str,
        task_name: str,
        task_meta: dict,
        workflow_type: str,
        run_id: int,
        outcome: str,
        energy_uj: int,
        orchestration_uj: int,
        compute_uj: int,
        gpu_energy_uj: int = None,
        conn=None,
    ) -> int:
        """
        Create one goal_execution + one goal_attempt for a completed single-attempt run.
 
        Single-attempt path only — no retry logic here. 8.5-B owns retry.
        Returns goal_id, or None if creation failed.
 
        Args:
            db:               DB adapter with .db.conn attribute.
            exp_id:           Parent experiment ID.
            task_id:          Task identifier string.
            task_name:        Human readable name for goal_description.
            task_meta:        Dict with optional 'level' and 'category' keys.
            workflow_type:    'linear' or 'agentic' — never 'comparison'.
            run_id:           Completed run_id from save_pair()/save_single().
            outcome:          Terminal outcome string.
            energy_uj:        Total energy snapshot.
            orchestration_uj: Orchestration energy snapshot.
            compute_uj:       Compute energy snapshot.
 
        Returns:
            goal_id (int) or None on failure.
        """
        if conn is None:
            conn = db.db.conn
 
        # Derive difficulty and goal_type from task metadata
        level = task_meta.get("level") if task_meta else None
        category = task_meta.get("category", "custom") if task_meta else "custom"
 
        # goal_id created with -1 placeholder for first_run_id — resolved below
        goal_id = _goal_tracker.start_goal(
            conn=conn,
            exp_id=exp_id,
            task_id=task_id,
            task_name=task_name,
            goal_type=category,
            workflow_type=workflow_type,
            difficulty_level=level,
            first_run_id=-1,
        )
        if goal_id is None:
            return None
 
        # Single attempt — no retry loop here, called from save_pair/save_single
        # which already have a completed run_id. Retry loop lives in _record_goal_with_retry.
        attempt_id = _goal_tracker.start_attempt(
            conn=conn,
            goal_id=goal_id,
            attempt_number=1,
            is_retry=False,
            retry_of_attempt_id=None,
        )
        if attempt_id is None:
            return goal_id
 
        # Classify failure type for non-success outcomes — prevents NULL failure_type in paper queries
        failure_type = None if outcome == "success" else _failure_classifier.classify(run_result=None)

        _goal_tracker.finish_attempt(
            conn=conn,
            attempt_id=attempt_id,
            run_id=run_id,
            outcome=outcome,
            energy_uj=energy_uj,
            orchestration_uj=orchestration_uj,
            compute_uj=compute_uj,
            failure_type=failure_type,
            gpu_energy_uj=gpu_energy_uj,
        )
        # G142: this attempt is recorded after its run, so start_attempt and
        # finish_attempt stamped bookkeeping times. Its window is the run's
        # measured window; one place for pair (both sides) and single.
        _ts = conn.execute(
            "SELECT start_time_ns, end_time_ns FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if _ts and _ts[0] is not None and _ts[1] is not None:
            conn.execute(
                "UPDATE goal_attempt SET started_at_ns = ?, finished_at_ns = ? WHERE attempt_id = ?",
                (_ts[0], _ts[1], attempt_id),
            )
            conn.commit()

        success = (outcome == "success")
        _goal_tracker.finish_goal(
            conn=conn,
            goal_id=goal_id,
            success=success,
            winning_run_id=run_id if success else None,
            total_attempts=1,
        )
 
        return goal_id
    
    def save_single(
            self,
            db,
            exp_id: int,
            hw_id: int,
            result: dict,
            rep_num: int,
            workflow_type: str,
            task_meta: dict = None,
            writer=None,
        ) -> int:
            """
            Save one run for single-workflow-mode experiments (linear or agentic only).

            Mirrors save_pair() exactly for one side. All sample types, ETL chain,
            duration fix, and goal tracking are identical to the corresponding side
            in save_pair(). Returns run_id or None on failure.

            Args:
                db:            DB adapter.
                exp_id:        Parent experiment ID.
                hw_id:         Hardware profile ID.
                result:        Harness result dict — same structure as save_pair() sides.
                rep_num:       Repetition number (1-indexed).
                workflow_type: 'linear' or 'agentic' only — never 'comparison'.
            """
            if workflow_type not in ("linear", "agentic"):
                logger.warning(
                    "save_single: invalid workflow_type=%r — must be linear or agentic",
                    workflow_type,
                )
                return None


            _wconn = db.db.conn
 
            # Mirror save_pair() pre-insert setup exactly
            result["ml_features"]["run_number"] = rep_num
            result_copy = result.copy()
            result_copy["baseline_id"] = result_copy["ml_features"].get("baseline_id")

            task_id   = result.get("task_id", "unknown")
            task_name = result.get("task_name", task_id)
            # Use passed task_meta (from run_experiment.py) if available,
            # otherwise fall back to what the harness put in the result dict.
            task_meta = task_meta or result.get("task_meta", {}) or {}
            # Inject task_meta into result so _run_quality_scoring can read it.
            result["task_meta"] = task_meta
            outcome   = "success" if result.get("execution", {}).get("status") == "success" else "failure"

            from core.vocabularies.agent.span_writer import SpanWriter
            _span_writer = SpanWriter()
            _span_id = _span_writer.open_span("run", f"{workflow_type}:{task_id}")
            _span_writer.close_span(_span_id)
            build_spans_from_result(_span_writer, _span_id, result, workflow_type, self.get_hardware_info())

            # All persistence through the one writer (C3, G77, G91).
            # C-EV stage recorder (39.5.2c); rows written after the run, also
            # when persistence raises; the exception propagates.
            from core.observability.stages import StageRecorder, persist_after_run
            _st = StageRecorder("save_single")
            try:
                run_id = RunPersistenceService().insert_one_run(
                    db, exp_id, hw_id, result, workflow_type, rep_num,
                    after_run_row=_span_hook(db, _wconn, _span_writer),
                    stages=_st,
                )
                _st.attach_run_id(run_id)
            finally:
                persist_after_run(_st, db)

            # SPEC 35J: energy_uj computation moved OUT of this block —
            # it must run unconditionally (save_pair()'s attributed energy rule
            # is never gated on thermal_samples presence either). Keeping
            # it inside here caused a real UnboundLocalError crash on
            # --workflow-mode agentic, the first time this path was ever
            # exercised, when thermal_samples was absent from result.
            # E4: same rule as save_pair and execute_goal.
            from core.execution.run_persistence import (
                attributed_energy_or_none, orchestration_energy_or_none)
            energy_uj = attributed_energy_or_none(result)
            orchestration_uj = orchestration_energy_or_none(result)


            # (energy_uj/orchestration_uj now computed above, unconditionally,
            # before this thermal_samples check — see SPEC 35J note above)
            # GPU PP1 energy for this run — None on non-Tiger-Lake
            _gpu_uj = result.get("ml_features", {}).get("gpu_dynamic_energy_uj")

            logger.info("save_single: run_id=%d workflow=%s rep=%d", run_id, workflow_type, rep_num)


            # Goal tracking — single side only
            _backfill_span_outcome(_wconn, run_id, outcome)
            goal_id = self._record_goal_pair(
                db=db,
                exp_id=exp_id,
                task_id=task_id,
                task_name=task_name,
                task_meta=task_meta,
                workflow_type=workflow_type,
                run_id=run_id,
                outcome=outcome,
                energy_uj=energy_uj,
                orchestration_uj=orchestration_uj,
                compute_uj=None,
                gpu_energy_uj=_gpu_uj,
                conn=_wconn,
            )

            if goal_id is not None:
                goal_execution_etl.process_one(goal_id, _wconn)
                _goal_tracker.queue_etl(
                    _wconn, "goal_execution", goal_id, "goal_execution_etl",
                )
                # Bug 7 fix: backfill attempt_id and ns timestamps for save_single path.
                if workflow_type == "agentic":
                    _attempt = _wconn.execute(
                        "SELECT attempt_id FROM goal_attempt WHERE run_id = ? LIMIT 1",
                        (run_id,)
                    ).fetchone()
                    if _attempt:
                        _run_ts = _wconn.execute(
                            "SELECT start_time_ns, end_time_ns FROM runs WHERE run_id = ? LIMIT 1",
                            (run_id,)
                        ).fetchone()
                        _wconn.execute(
                            "UPDATE orchestration_events SET attempt_id = ? WHERE run_id = ? AND attempt_id IS NULL",
                            (_attempt[0], run_id)
                        )
                        if _run_ts:
                            _wconn.execute(
                                "UPDATE goal_attempt SET started_at_ns = ?, finished_at_ns = ? WHERE attempt_id = ?",
                                (_run_ts[0], _run_ts[1], _attempt[0])
                            )
                        _wconn.commit()

            # --- Quality scoring (8.5C) ---
            _qe = getattr(self.args, "quality_enabled", False) if hasattr(self, "args") else False
            if goal_id is not None:
                _run_quality_scoring(db=db, goal_id=goal_id, result=result,
                                     workflow_type=result.get("workflow_type", "linear"),
                                     conn=_wconn, quality_enabled=_qe)
            _write_quality_annotations(db, _wconn, run_id)

            energy_attribution_etl.populate_attribution_stubs(run_id, _wconn)
            _goal_tracker.queue_etl(_wconn, "run", run_id, "energy_attribution_etl")
            return run_id

