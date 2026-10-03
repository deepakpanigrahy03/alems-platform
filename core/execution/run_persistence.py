"""
run_persistence.py — Single owner of run insertion, sample storage, and ETL chain.

Extracted from ExperimentRunner so both ExperimentRunner (normal path) and
GoalExecutionManager (retry path) share identical run persistence logic.
Neither path duplicates — both delegate here.

Design:
    RunPersistenceService.insert_one_run()
        → _insert_run_row()       insert run + provenance + validate + normalization_factors stub
        → _insert_samples()       all sample tables
        → _insert_events()        orchestration events + LLM interactions
        → _run_post_etl()         full ETL chain (sync)
        → _apply_duration_fix()   fix_run / fix_run_with_pretask

Energy units: all µJ (REAL). NULL = not yet computed, 0 = computed and is zero.
All operations are synchronous — no async, no threads.
"""

import logging
from typing import Optional

from core.utils.provenance import record_run_provenance
from core.attribution.legacy_v1.phase_attribution_etl import compute_phase_attribution
from core.attribution.legacy_v1.aggregate_hardware_metrics import aggregate_hardware_metrics
from core.attribution.legacy_v1.energy_attribution_etl import compute_energy_attribution, populate_tool_failure_wasted_energy
from core.attribution.legacy_v1.duration_fix_etl import fix_run, fix_run_with_pretask
from core.attribution.legacy_v1.ttft_tpot_etl import populate_run as populate_ttft_tpot
from core.attribution.conservation_residual import compute_conservation_residual


logger = logging.getLogger(__name__)


def aggregate_run_stats(run_id, cpu_samples, interrupt_samples, thermal_samples=None):
    # type: (int, list, list, Optional[list]) -> dict
    """
    Aggregate hardware stats for one run (single implementation, all paths).

    Moved unchanged from ExperimentRunner.aggregate_run_stats. Temperatures use
    calculate_thermal_metrics, the same function the harness uses for
    start_temp_c, so they can never diverge (TD-1).

    Returns:
        dict for db.update_run_stats().
    """
    from core.execution.sample_processor import calculate_thermal_metrics

    stats = {
        "run_id": run_id,
        # G90: None until measured; 0.0 stored "not measured" as a value (INV-E1).
        "cpu_busy_mhz": None,
        "cpu_avg_mhz": None,
        "package_temp_celsius": None,
        "max_temp_c": None,
        "min_temp_c": None,
        "interrupt_rate": None,
    }
    if cpu_samples:
        busy = [s.get("cpu_busy_mhz", 0) for s in cpu_samples if s.get("cpu_busy_mhz")]
        avg = [s.get("cpu_avg_mhz", 0) for s in cpu_samples if s.get("cpu_avg_mhz")]
        if busy:
            stats["cpu_busy_mhz"] = sum(busy) / len(busy)
        if avg:
            stats["cpu_avg_mhz"] = sum(avg) / len(avg)
    start_c, max_c, min_c, _delta = calculate_thermal_metrics(cpu_samples, thermal_samples)
    if start_c:
        stats["package_temp_celsius"] = start_c
        stats["max_temp_c"] = max_c
        stats["min_temp_c"] = min_c
    if interrupt_samples:
        rates = [s.get("interrupts_per_sec", 0) for s in interrupt_samples
                 if s.get("interrupts_per_sec")]
        if rates:
            stats["interrupt_rate"] = sum(rates) / len(rates)
    return stats


def attributed_energy_or_none(result):
    # type: (Optional[dict]) -> Optional[int]
    """
    Attributed workload energy (E_attr) of one run or attempt.

    The only quantity allowed for goal and attempt energy (INV-E5). Returns the
    harness value, a measured 0 included; None when the result or the value is
    absent. Never substitutes dynamic, workload or 0 (E4, INV-E1).
    """
    if not result:
        return None
    value = (result.get("ml_features") or {}).get("attributed_energy_uj")
    if value is None:
        import logging
        logging.getLogger(__name__).warning(
            "attributed_energy_uj absent: goal and attempt energy stored as NULL")
        return None
    return int(value)


def orchestration_energy_or_none(result):
    # type: (Optional[dict]) -> Optional[int]
    """Orchestration energy as stored today (layer3 orchestration_tax); None when absent."""
    if not result:
        return None
    try:
        value = result["layer3_derived"]["energy_uj"].get("orchestration_tax")
    except (KeyError, TypeError, AttributeError):
        return None
    return None if value is None else int(value)


def derive_run_fields(agg, ml):
    # type: (dict, dict) -> dict
    """
    Derived run columns from ml_features (single implementation, all paths).

    Moved unchanged from save_pair: frequency fallback where turbostat is absent
    (ARM), avg_task_power_watts, energy_sample_coverage_pct (SPBM, then phase,
    then IOKit sample count), framework_overhead_energy_uj, gpu_attribution_method.
    Every value is None or absent when its inputs are unavailable (PAC-4).
    """
    ml = ml or {}
    if not agg.get("cpu_avg_mhz") and ml.get("frequency_mhz"):
        agg["cpu_avg_mhz"] = ml["frequency_mhz"]
        agg["cpu_busy_mhz"] = ml["frequency_mhz"]
    task_dur_s = ml.get("task_duration_sec") or 0
    fw_s = ml.get("framework_overhead_sec") or 0
    attr_uj = ml.get("attributed_energy_uj") or 0
    spbm_cov = (ml.get("spbm_telemetry_coverage") or {}).get("spbm_sample_coverage_pct")
    phase_cov = ml.get("phase_sample_coverage_pct")
    gpu_dynamic = ml.get("gpu_dynamic_energy_uj") or 0
    gpu_spbm = ml.get("gpu_total_energy_uj") or 0
    if attr_uj and task_dur_s:
        agg["avg_task_power_watts"] = round(attr_uj / 1_000_000.0 / task_dur_s, 4)
    # IOKit samples at 200 ms (5 Hz); used only when SPBM and phase are absent.
    iokit_cov = None
    if spbm_cov is None and phase_cov is None:
        dur_ns = ml.get("task_duration_ns") or 0
        count = ml.get("energy_sample_count") or 0
        if dur_ns > 0 and count > 0:
            iokit_cov = min((count * 200_000_000) / dur_ns * 100, 100.0)
    agg["energy_sample_coverage_pct"] = (
        spbm_cov if spbm_cov is not None else
        phase_cov if phase_cov is not None else iokit_cov)
    if agg.get("avg_task_power_watts") and fw_s:
        agg["framework_overhead_energy_uj"] = round(agg["avg_task_power_watts"] * fw_s * 1_000_000)
    is_spbm = ml.get("spbm_telemetry_coverage") is not None
    if is_spbm and gpu_dynamic > 0:
        agg["gpu_attribution_method"] = "dcgm_field156"
    elif is_spbm and gpu_spbm > 0:
        agg["gpu_attribution_method"] = "spbm_package_v1"
    elif ml.get("reader_method_id") == "iokit_power_reader" and gpu_spbm > 0:
        agg["gpu_attribution_method"] = "iokit_powermetrics"
    elif not is_spbm and gpu_spbm > 0:
        agg["gpu_attribution_method"] = "pp1_msr"
    else:
        agg["gpu_attribution_method"] = "none"
    return agg


def finalize_run_stats(db, run_id, result):
    # type: (object, int, dict) -> None
    """
    Aggregate, derive, and store run stats; always runs (IMPROVEMENTS 11.3).

    Falls back to the sample count coverage query when no coverage is known.
    """
    agg = aggregate_run_stats(
        run_id,
        result.get("cpu_samples", []),
        result.get("interrupt_samples", []),
        result.get("thermal_samples", []),
    )
    derive_run_fields(agg, result.get("ml_features"))
    db.update_run_stats(run_id, agg)
    if not agg.get("energy_sample_coverage_pct"):
        db.runs.update_energy_sample_coverage(run_id)


def _get_platform_arch() -> str:
    """
    Read cpu_architecture from config/hw_config.json.

    RunPersistenceService is stateless (no self.config), so this reads
    hw_config.json directly rather than relying on stored state — mirrors
    ExperimentRunner.get_hardware_info()'s metadata.machine field without
    requiring access to a config_loader instance.

    Returns 'aarch64' on GN100, 'x86_64' on x86_64 platforms, '' if unavailable.
    """
    import json
    from pathlib import Path
    hw_config_path = Path("config/hw_config.json")
    if not hw_config_path.exists():
        return ""
    try:
        with open(hw_config_path) as f:
            data = json.load(f)
        return (data.get("metadata", {}).get("machine") or "").lower()
    except Exception:
        return ""


class PersistenceError(RuntimeError):
    """A run could not be persisted. Raised so no path can lose a run silently (G88)."""


class RunPersistenceService:
    """
    Owns the full lifecycle of persisting one harness result to the DB.

    Stateless — safe to instantiate once at module level and reuse.
    All methods take explicit db/conn arguments — no stored state.
    """

    def insert_one_run(
        self,
        db,
        exp_id: int,
        hw_id: int,
        result: dict,
        workflow_type: str,
        rep_num: int,
        after_run_row=None,
    ) -> Optional[int]:
        """
        Persist one completed harness result with all samples and ETL.

        Single entry point for both normal and retry paths — guarantees
        identical 120-column run correctness regardless of caller.

        Args:
            db:            DB adapter with insert_* and transaction() methods.
            exp_id:        Parent experiment ID.
            hw_id:         Hardware profile ID.
            result:        Full harness result dict.
            workflow_type: 'linear' or 'agentic' — never 'comparison'.
            rep_num:       Repetition number (1-indexed) for run_number field.

        Returns:
            run_id (int) or None on failure.
        """
        if workflow_type not in ("linear", "agentic"):
            raise PersistenceError("invalid workflow_type=%r" % (workflow_type,))

        # Same order as before: raw transaction, then derived steps (G137 split).
        run_id = self.persist_raw(db, exp_id, hw_id, result, workflow_type,
                                  rep_num, after_run_row)
        self.run_derived(db, run_id, result)
        return run_id

    def persist_raw(
        self,
        db,
        exp_id: int,
        hw_id: int,
        result: dict,
        workflow_type: str,
        rep_num: int,
        after_run_row=None,
    ) -> int:
        """
        Stage 1 (G137): run row, samples and events in one transaction.

        Durable raw data first; nothing here depends on an ETL, so a failing
        derived step can never roll this back.

        Returns:
            run_id of the committed run.
        """
        if workflow_type not in ("linear", "agentic"):
            raise PersistenceError("invalid workflow_type=%r" % (workflow_type,))

        # run_number must be stamped before insert so ETL sees it
        result["ml_features"]["run_number"] = rep_num

        with db.transaction():
            run_id = self._insert_run_row(db, exp_id, hw_id, result, workflow_type)
            if run_id is None:
                # Visible on every path (G88); the transaction rolls back.
                raise PersistenceError(
                    "insert_run returned None (exp_id=%s, workflow=%s)" % (exp_id, workflow_type)
                )
            if after_run_row is not None:
                # Caller hook inside the transaction, right after the run row:
                # span flush and attempt span id backfill (EEI-4).
                after_run_row(run_id)
            self._insert_samples(db, run_id, result)
            self._insert_events(db, run_id, result)
        return run_id

    def run_derived(self, db, run_id: int, result: dict) -> None:
        """
        Stage 2 (G137): steps that read committed samples, ETL, duration fix,
        residual. Runs only after persist_raw committed.
        """
        # Steps that commit on their own or read committed samples (G73).
        self._insert_after_commit(db, run_id, result)

        # ETL runs outside transaction — each ETL function is idempotent
        self._run_post_etl(run_id)
        self._apply_duration_fix(run_id, result)
        self._compute_residual(run_id, db)

    # ── Private helpers — each does exactly one thing ─────────────────────────

    def _insert_run_row(
        self,
        db,
        exp_id: int,
        hw_id: int,
        result: dict,
        workflow_type: str,
    ) -> Optional[int]:
        """
        Insert run row, provenance, quality score, and normalization_factors stub.

        normalization_factors stub inserted here so ETL backfill never skips.
        Only run_id, task_category, workload_type known at this point — all
        other columns are NULL and populated by ETL later.

        Returns run_id or None on failure.
        """
        run_id = db.insert_run(exp_id, hw_id, result)
        if run_id is None:
            logger.warning("_insert_run_row: insert_run returned None")
            return None

        # Provenance must be recorded immediately after insert
        record_run_provenance(
            db, run_id, result, reader_mode=result.get("reader_mode")
        )

        # Quality score — written to run_quality table
        self._score_run(db, run_id, hw_id)

        # Stub row so ETL _backfill_normalization_factors never skips this run
        # All metric columns are NULL — ETL populates them after goal tracking
        task_meta = result.get("task_meta", {}) or {}
        task_category = task_meta.get("category", "custom")
        db.db.execute(
            """INSERT OR IGNORE INTO normalization_factors
               (run_id, task_category, workload_type)
               VALUES (?, ?, ?)""",
            (run_id, task_category, workflow_type),
        )

        return run_id

    def _score_run(self, db, run_id: int, hw_id) -> None:
        """
        Score run quality and insert into run_quality table.
        Mirrors ExperimentRunner._validate_run() exactly.
        hw_id may be None for retry-path calls — scorer handles gracefully.
        """
        from core.utils.quality_scorer import QualityScorer

        run = db.get_run(run_id)
        hardware_hash = "default"
        if hw_id is not None:
            hw_rows = db.db.execute(
                "SELECT hardware_hash FROM hardware_config WHERE hw_id = ?", (hw_id,)
            )
            if hw_rows:
                hardware_hash = hw_rows[0]["hardware_hash"]

        scorer = QualityScorer()
        valid, score, reason = scorer.compute(run or {}, hardware_hash)
        db.db.execute(
            """INSERT OR REPLACE INTO run_quality
               (run_id, experiment_valid, quality_score, rejection_reason, quality_version)
               VALUES (?, ?, ?, ?, ?)""",
            (run_id, valid, score, reason, scorer.VERSION),
        )

    def _insert_samples(self, db, run_id: int, result: dict) -> None:
        """
        Insert all sample tables for one run.

        Handles old tuple format for backward compat with pre-Chunk-2 energy samples.
        aggregate_run_stats + update_run_stats called inside thermal block — mirrors
        save_pair() exactly so aggregated hardware stats are always populated.
        """
        # Energy samples — convert old tuple format if present
        if "energy_samples" in result:
            converted = self._convert_energy_samples(result["energy_samples"])
            if converted:
                db.insert_energy_samples(run_id, converted)

        # From here: the save_pair per run block (source of truth), so every
        # execution path persists the same tables (EPS-1, G77). Each step is a
        # no op when the harness did not produce its input on this platform.
        if result.get("gpu_samples"):
            db.insert_gpu_samples(run_id, result["gpu_samples"])
            try:
                # Late import: experiment_runner imports this module.
                from core.execution.experiment_runner import _convert_gpu_to_telemetry
                telemetry = _convert_gpu_to_telemetry(result["gpu_samples"])
                if telemetry:
                    db.insert_device_telemetry(run_id, telemetry)
            except Exception as _e:
                logger.warning("device_telemetry insert failed run_id=%d: %s", run_id, _e)
        # v2 and SPBM are independent keys, as in save_pair; the harness fills
        # at most one of them on any platform.
        if result.get("v2_samples"):
            db.insert_energy_samples_v2(run_id, result["v2_samples"])
        if result.get("legacy_samples"):
            db.insert_energy_samples(run_id, result["legacy_samples"])
        if result.get("spbm_samples"):
            db.insert_energy_samples_v2(run_id, result["spbm_samples"])
        if result.get("rail_result"):
            try:
                db.insert_power_rail_samples(run_id, result["rail_result"].samples)
                db.insert_run_power_limits(run_id, result["rail_result"].limits_snapshot)
            except Exception as _e:
                logger.warning("power_rail insert failed run_id=%d: %s", run_id, _e)

        if "cpu_samples" in result:
            db.insert_cpu_samples(run_id, result["cpu_samples"])

        # One summary cpu_samples row where turbostat is absent (save_pair 16D3):
        # aarch64 from ARM PMU counters, Darwin from KPerf. x86 wrote rows above.
        self._insert_summary_cpu_row(db, run_id, result)

        # 16D3: ARM — no PerformanceCounters object available in result dict here
        # (unlike experiment_runner.py's linear_result/agentic_result). The retry
        # path that calls insert_one_run() does not currently carry raw perf
        # counters through to this layer. cpu_idle_states still works on both
        # platforms below since it only needs cpu_samples (x86) or sysfs (ARM).
        _rp_arch = _get_platform_arch()

        # cpu_idle_states: ARM path — cpuidle sysfs cumulative residency
        if _rp_arch == "aarch64":
            try:
                db.cpu_idle.write_from_cpuidle_sysfs(run_id, platform="grace_aarch64")
            except Exception as _e:
                logger.warning("cpu_idle_states ARM insert failed run_id=%d: %s", run_id, _e)
        else:
            # cpu_idle_states: x86 path — prefer turbostat, fall back to cpuidle sysfs
            # AMD Zen 2 turbostat crashes (SIGABRT in rapl_perf_init), cpu_samples
            # is empty. cpuidle sysfs verified working on AMD. Vendor check added
            # here because this site (unlike experiment_runner) hardcoded intel.
            try:
                _is_amd = False
                try:
                    with open("/proc/cpuinfo") as _f:
                        _is_amd = "AuthenticAMD" in _f.read()
                except (IOError, OSError):
                    _is_amd = False
                _idle_platform = "amd_x86_64" if _is_amd else "intel_x86_64"
                _cpu_samples = result.get("cpu_samples", [])
                if _cpu_samples:
                    db.cpu_idle.write_from_turbostat(
                        run_id,
                        _cpu_samples,
                        platform=_idle_platform,
                    )
                elif os.path.exists("/sys/devices/system/cpu/cpu0/cpuidle/state0"):
                    db.cpu_idle.write_from_cpuidle_sysfs(run_id, platform=_idle_platform)
                else:
                    logger.info("cpu_idle_states: no turbostat data and no cpuidle sysfs, skipping run_id=%d", run_id)
            except Exception as _e:
                logger.warning("cpu_idle_states x86 insert failed run_id=%d: %s", run_id, _e)

        # 16D2a: cooling_samples — end-of-run snapshot
        try:
            import socket as _socket_cool
            db.cooling.snapshot_cooling_state(run_id, _socket_cool.gethostname().lower())
        except Exception as _e:
            logger.warning("cooling_samples insert failed run_id=%d: %s", run_id, _e)

        if "interrupt_samples" in result:
            db.insert_interrupt_samples(run_id, result["interrupt_samples"])

        if "io_samples" in result:
            db.insert_io_samples(run_id, result["io_samples"])

        if "thermal_samples" in result:
            db.insert_thermal_samples(run_id, result["thermal_samples"])
            # Thermal V2 per zone rows, as save_pair and save_single (G74).
            try:
                import socket as _socket_v2
                db.thermal.insert_thermal_samples_v2(
                    run_id, result["thermal_samples"], _socket_v2.gethostname().lower(),
                )
            except Exception as _e:
                logger.warning("thermal_samples_v2 insert failed run_id=%d: %s", run_id, _e)

        # One aggregation and derivation for every path, always run (G83, 11.3).
        finalize_run_stats(db, run_id, result)

    def _insert_summary_cpu_row(self, db, run_id: int, result: dict) -> None:
        """
        Insert one summary cpu_samples row on aarch64 or Darwin (save_pair 16D3).

        The row spans the run window, read from the runs row just inserted.
        Never raises: a failed summary row must not abort the run (PAC-4).
        """
        import platform as _platform
        from core.execution.arm_cpu_sample_builder import _build_arm_cpu_sample_row
        from core.execution.darwin_cpu_sample_builder import _build_darwin_cpu_sample_row

        if _get_platform_arch() == "aarch64":
            row = _build_arm_cpu_sample_row(run_id, result)
        elif _platform.system() == "Darwin":
            row = _build_darwin_cpu_sample_row(run_id, result)
        else:
            return
        if not row:
            return
        run = db.get_run(run_id)
        if run:
            row["sample_start_ns"] = run.get("start_time_ns")
            row["sample_end_ns"] = run.get("end_time_ns")
            row["timestamp_ns"] = run.get("end_time_ns")
            if _platform.system() == "Darwin":
                row["interval_ns"] = (run.get("end_time_ns") or 0) - (run.get("start_time_ns") or 0)
        try:
            db.insert_cpu_samples(run_id, [row])
        except Exception as _e:
            logger.warning("summary cpu_samples insert failed run_id=%d: %s", run_id, _e)

    def _insert_after_commit(self, db, run_id: int, result: dict) -> None:
        """
        NIC samples, then SPBM and network ETLs, after the sample transaction.

        _insert_nic_samples commits on its connection, so it must not run inside
        the transaction; the ETLs read committed rows only. Same connection as
        the adapter (no second writer connection).
        """
        try:
            conn = db.db.conn
        except AttributeError:
            conn = getattr(db, "conn", None)
        from core.execution.experiment_runner import _insert_nic_samples  # late: cycle
        if result.get("nic_samples"):
            _insert_nic_samples(db, run_id, result["nic_samples"], conn=conn)
        # Transitional: core imports scripts here, as before (CH39-3, G81, 39.5.5).
        try:
            from scripts.etl.gpu_spbm_etl import process_one as _pgs
            _pgs(run_id, conn)
        except Exception as _e:
            logger.warning("gpu_spbm_etl failed run_id=%d: %s", run_id, _e)
        try:
            from scripts.etl.spbm_telemetry_etl import process_run as _pst
            _pst(run_id, result, conn)
        except Exception as _e:
            logger.warning("spbm_telemetry_etl failed run_id=%d: %s", run_id, _e)
        try:
            from scripts.etl.network_energy_etl import process_run as _pne
            _pne(run_id, conn)
        except Exception as _e:
            logger.warning("network_etl failed run_id=%d: %s", run_id, _e)

    def _insert_events(self, db, run_id: int, result: dict) -> None:
        """
        Insert orchestration events and LLM interactions.

        LLM interactions key is pending_interactions — run_id stamped per row
        because harness does not know run_id at capture time.
        """
        # Same key fallback execute_goal used; inserted exactly once (G82).
        events = (result.get("orchestration_events")
                  or (result.get("execution") or {}).get("events")
                  or result.get("events"))
        if events:
            db.insert_orchestration_events(run_id, events)

        # pending_interactions — harness key for not-yet-persisted LLM calls
        if result.get("pending_interactions"):
            for interaction in result["pending_interactions"]:
                interaction["run_id"] = run_id  # stamp run_id before insert
                db.insert_llm_interaction(interaction)

    def _run_post_etl(self, run_id: int) -> None:
        """
        Run full ETL chain for one run. Same order as save_pair().
        All ETL functions are idempotent — safe to rerun on same run_id.
        Sync only — no async, no threads (confirmed: experiment_runner has zero async).
        """
        compute_phase_attribution(run_id)       # step 1: event_energy_uj on orchestration_events
        aggregate_hardware_metrics(run_id)      # step 2: run aggregate columns
        populate_tool_failure_wasted_energy(run_id)  # step 3: copy event_energy_uj → tfe.wasted_energy_uj
        compute_energy_attribution(run_id)      # step 4: reads tfe for failed_tool_energy_uj
        populate_ttft_tpot(run_id)              # step 5: token timing metrics


    def _apply_duration_fix(self, run_id: int, result: dict) -> None:
        """
        Apply duration correction. Mirrors save_pair() fix block exactly.

        fix_run_with_pretask used when pre-task RAPL snapshot exists.
        fix_run used otherwise. Never skip — duration fix affects energy attribution.
        """
        ml = result.get("ml_features", {})
        if ml.get("rapl_before_pretask") is not None:
            fix_run_with_pretask(
                run_id,
                ml.get("rapl_before_pretask"),
                ml.get("rapl_after_task"),
                ml.get("pre_task_duration_sec", 0.0),
                ml.get("post_task_duration_sec", 0.0),
                ml.get("cpu_frac_pre", 0.0),
                ml.get("cpu_frac_post", 0.0),
            )
        else:
            fix_run(run_id)

    def _compute_residual(self, run_id: int, db: object) -> None:
        # type: (int, object) -> None
        """Populate attribution_residual. Transitional raw conn (B39-4c-1)."""
        try:
            conn = db.db.conn
        except AttributeError:
            conn = getattr(db, "conn", None)
        if conn is None:
            logger.warning("_compute_residual: no conn for run_id=%d", run_id)
            return
        compute_conservation_residual(run_id, conn)
        
    def _convert_energy_samples(self, samples: list) -> list:
        """
        Convert energy samples to dict format.

        Handles backward compat with old tuple format (timestamp, energy_dict)
        from pre-Chunk-2 harness. New dict format passed through unchanged.
        """
        converted = []
        for sample in samples:
            if isinstance(sample, dict):
                # Chunk 2+ format — use directly
                converted.append(sample)
            elif len(sample) == 2 and isinstance(sample[1], dict):
                # Old tuple format — convert to dict
                timestamp, energy_dict = sample
                converted.append({
                    "timestamp_ns":     int(timestamp * 1_000_000_000),
                    "pkg_energy_uj":    energy_dict.get("package-0", 0),
                    "core_energy_uj":   energy_dict.get("core", 0),
                    "uncore_energy_uj": energy_dict.get("uncore", 0),
                    "dram_energy_uj":   0,
                })
        return converted



# Module-level singleton — stateless, safe to share
_persistence = RunPersistenceService()


def insert_one_run(
    db,
    exp_id: int,
    hw_id: int,
    result: dict,
    workflow_type: str,
    rep_num: int,
) -> Optional[int]:
    """
    Module-level convenience wrapper around RunPersistenceService.insert_one_run().

    Both ExperimentRunner and GoalExecutionManager import this function —
    no need to instantiate the service directly.
    """
    return _persistence.insert_one_run(db, exp_id, hw_id, result, workflow_type, rep_num)


def persist_raw(db, exp_id, hw_id, result, workflow_type, rep_num):
    """Module wrapper: stage 1 of one attempt run (G137)."""
    return _persistence.persist_raw(db, exp_id, hw_id, result, workflow_type, rep_num)


def run_derived(db, run_id, result):
    """Module wrapper: stage 2 of one committed run (G137)."""
    return _persistence.run_derived(db, run_id, result)
