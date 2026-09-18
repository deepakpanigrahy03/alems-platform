"""
scripts/etl/energy_window_resolver.py
================================================================================
PURPOSE:
    Platform-aware energy window resolvers for pre-task and post-task energy
    attribution. Each platform stores energy samples differently — this module
    provides a clean ABC and concrete implementations so fix_run_with_pretask()
    has zero platform branching.

PLATFORM COVERAGE:
    RaplLegacyResolver   — x86 Intel/AMD: energy_samples (cumulative counters)
    SpbmV2Resolver       — GN100 SPBM ARM: energy_sample_domains (deltas) + point reads
    IokitV2Resolver      — Apple Silicon: energy_sample_domains (deltas), no point reads
    NullResolver         — platforms with no energy data (returns None gracefully)

ADDING A NEW PLATFORM:
    1. Subclass EnergyWindowResolverABC.
    2. Implement resolve_pre_task() and resolve_post_task().
    3. Register in EnergyWindowResolverFactory.detect().
    PAC-1: ABC first. Never instantiate directly.

DESIGN PRINCIPLES:
    - Zero platform branching in callers — factory picks resolver, caller calls methods.
    - All methods return None on unavailable data (PAC graceful degradation).
    - Raw sample energy used for overhead windows — no cpu_fraction, no baseline.
      Overhead windows are pure framework work; attribution is the window itself.
    - Point reads (cumulative counters) used only when available for pre-task delta.
    - Power extrapolation used only as last resort (IOKit) — documented as INFERRED.

DC-1: ~30% inline comment coverage maintained throughout.
DC-2: Docstrings on every method.
MPC-1: No new runs columns — no provenance entry needed.
PAC-2: No platform-conditional imports — factory handles detection.

Author: A-LEMS platform
"""

from __future__ import annotations

import logging
import sqlite3
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass — resolver output, consumed by fix_run_with_pretask()
# ---------------------------------------------------------------------------

class WindowEnergyResult:
    """
    Output of a resolver's resolve_pre_task() or resolve_post_task() call.

    raw_uj:      Total hardware energy in the window (µJ), no attribution applied.
    attributed_uj: Energy attributed to A-LEMS process (µJ). For overhead windows
                   this equals raw_uj (framework IS the process). For INFERRED
                   values this is an extrapolation — see method field.
    method:      Provenance string: 'MEASURED_DELTA' | 'MEASURED_SUM' | 'INFERRED_POWER'
    duration_ns: Window duration in nanoseconds.
    """

    __slots__ = ("raw_uj", "attributed_uj", "method", "duration_ns")

    def __init__(
        self,
        raw_uj: int,
        attributed_uj: int,
        method: str,
        duration_ns: int,
    ) -> None:
        self.raw_uj        = raw_uj
        self.attributed_uj = attributed_uj
        self.method        = method
        self.duration_ns   = duration_ns

    def __repr__(self) -> str:
        return (
            f"WindowEnergyResult(attributed={self.attributed_uj}µJ "
            f"raw={self.raw_uj}µJ method={self.method} "
            f"dur={self.duration_ns//1_000_000}ms)"
        )


# ---------------------------------------------------------------------------
# ABC
# ---------------------------------------------------------------------------

class EnergyWindowResolverABC(ABC):
    """
    Abstract base for all energy window resolvers.

    Each resolver handles one platform's sample storage format.
    Callers never branch on platform — they call resolve_pre_task()
    and resolve_post_task() and receive a WindowEnergyResult or None.

    PAC-1: every resolver MUST inherit this class.
    """

    @abstractmethod
    def resolve_pre_task(
        self,
        cursor: sqlite3.Cursor,
        run_id: int,
        rapl_before_uj: Optional[int],
        pre_task_duration_sec: float,
        cpu_frac_pre: float,
    ) -> Optional[WindowEnergyResult]:
        """
        Compute energy consumed in the pre-task window [t_before, t0].

        t_before: RAPL point read before instrumentation.
        t0:       start_measurement() — first sample timestamp.

        Args:
            cursor:               Open DB cursor.
            run_id:               Target run.
            rapl_before_uj:       Cumulative pkg counter at t_before. None if unavailable.
            pre_task_duration_sec: Duration of pre-task window in seconds.
            cpu_frac_pre:         A-LEMS process CPU share during pre-task.

        Returns:
            WindowEnergyResult or None if data unavailable.
        """

    @abstractmethod
    def resolve_post_task(
        self,
        cursor: sqlite3.Cursor,
        run_id: int,
        rapl_after_uj: Optional[int],
        t1_ns: int,
        post_task_duration_sec: float,
        cpu_frac_post: float,
    ) -> Optional[WindowEnergyResult]:
        """
        Compute energy consumed in the post-task window [t1, t2].

        t1: stop_measurement() — last sample timestamp.
        t2: end of post-task processing (DB writes, logging).

        Args:
            cursor:                Open DB cursor.
            run_id:                Target run.
            rapl_after_uj:         Cumulative pkg counter at t2. None if unavailable.
            t1_ns:                 Nanosecond timestamp of t1 (start_time_ns + task_duration_ns).
            post_task_duration_sec: Duration of post-task window in seconds.
            cpu_frac_post:         A-LEMS process CPU share during post-task.

        Returns:
            WindowEnergyResult or None if data unavailable.
        """

    @abstractmethod
    def name(self) -> str:
        """Human-readable resolver name for logging."""


# ---------------------------------------------------------------------------
# NullResolver — no energy data available
# ---------------------------------------------------------------------------

class NullResolver(EnergyWindowResolverABC):
    """
    Returns None for all windows.

    Used when a platform has no energy measurement capability,
    or when a run predates energy capture implementation.
    PAC graceful degradation: never raises, never logs warnings.
    """

    def resolve_pre_task(self, cursor, run_id, rapl_before_uj,
                         pre_task_duration_sec, cpu_frac_pre):
        """No data — return None."""
        return None

    def resolve_post_task(self, cursor, run_id, rapl_after_uj,
                          t1_ns, post_task_duration_sec, cpu_frac_post):
        """No data — return None."""
        return None

    def name(self) -> str:
        return "NullResolver"


# ---------------------------------------------------------------------------
# RaplLegacyResolver — x86 Intel/AMD (energy_samples table)
# ---------------------------------------------------------------------------

class RaplLegacyResolver(EnergyWindowResolverABC):
    """
    x86 Intel and AMD RAPL via energy_samples (legacy table).

    energy_samples stores cumulative pkg counters (pkg_start_uj, pkg_end_uj)
    per sample interval. Per-sample delta = pkg_end_uj - pkg_start_uj.

    Pre-task: cumulative delta from rapl_before_uj to first sample's pkg_start_uj.
    Post-task: SUM of per-sample deltas for samples after t1.

    Requires rapl_before_uj (point read) for pre-task.
    Post-task uses timestamp-filtered SUM — no point read needed.
    """

    def resolve_pre_task(
        self,
        cursor: sqlite3.Cursor,
        run_id: int,
        rapl_before_uj: Optional[int],
        pre_task_duration_sec: float,
        cpu_frac_pre: float,
    ) -> Optional[WindowEnergyResult]:
        """
        Pre-task = rapl_t0 - rapl_before.
        rapl_t0 = MIN(pkg_start_uj) — first sample's cumulative counter value.
        Returns None if rapl_before_uj unavailable (old runs without point reads).
        """
        if rapl_before_uj is None:
            # Cannot compute without the t_before anchor point read.
            logger.debug("Run %d: RaplLegacy pre-task — no rapl_before_uj", run_id)
            return None

        cursor.execute(
            "SELECT MIN(pkg_start_uj) FROM energy_samples WHERE run_id = ?",
            (run_id,)
        )
        row = cursor.fetchone()
        if not row or row[0] is None:
            return None

        rapl_t0_uj = int(row[0])
        raw_uj = max(0, rapl_t0_uj - rapl_before_uj)
        dur_ns = int(pre_task_duration_sec * 1_000_000_000)

        return WindowEnergyResult(
            raw_uj=raw_uj,
            attributed_uj=raw_uj,   # overhead window: raw IS attributed
            method="MEASURED_DELTA",
            duration_ns=dur_ns,
        )

    def resolve_post_task(
        self,
        cursor: sqlite3.Cursor,
        run_id: int,
        rapl_after_uj: Optional[int],
        t1_ns: int,
        post_task_duration_sec: float,
        cpu_frac_post: float,
    ) -> Optional[WindowEnergyResult]:
        """
        Post-task = SUM of (pkg_end_uj - pkg_start_uj) for samples after t1.
        Uses timestamp_ns filter — no point read needed.
        Typically 0 or 1 sample (sampling stops near stop_measurement()).
        """
        cursor.execute("""
            SELECT COALESCE(SUM(pkg_end_uj - pkg_start_uj), 0)
            FROM energy_samples es
            JOIN runs r ON r.run_id = es.run_id
            WHERE es.run_id = ?
              AND es.timestamp_ns > (r.start_time_ns + COALESCE(r.task_duration_ns, 0))
        """, (run_id,))
        row = cursor.fetchone()
        raw_uj = int(row[0] or 0)
        dur_ns = int(post_task_duration_sec * 1_000_000_000)

        return WindowEnergyResult(
            raw_uj=raw_uj,
            attributed_uj=raw_uj,   # overhead window: raw IS attributed
            method="MEASURED_SUM",
            duration_ns=dur_ns,
        )

    def name(self) -> str:
        return "RaplLegacyResolver"


# ---------------------------------------------------------------------------
# SpbmV2Resolver — GN100 SPBM ARM (energy_sample_domains + point reads)
# ---------------------------------------------------------------------------

class SpbmV2Resolver(EnergyWindowResolverABC):
    """
    NVIDIA Grace GB10 (GN100) SPBM energy via energy_sample_domains.

    energy_sample_domains stores per-interval energy deltas (not cumulative).
    Point reads (rapl_before_uj, rapl_after_uj) are cumulative counters from
    read_energy() and span the full run [t_before, t2].

    Pre-task: total_point_delta - task_sum - post_task_sum.
    Post-task: SUM of domain deltas for samples after t1.

    Both require the PACKAGE domain (root domain with no parent).
    """

    def __init__(self, pkg_domain_id: int) -> None:
        """
        Args:
            pkg_domain_id: energy_domains.domain_id for the root PACKAGE domain.
        """
        # Cache domain_id so both methods share it without re-querying.
        self._pkg_domain_id = pkg_domain_id

    def resolve_pre_task(
        self,
        cursor: sqlite3.Cursor,
        run_id: int,
        rapl_before_uj: Optional[int],
        pre_task_duration_sec: float,
        cpu_frac_pre: float,
    ) -> Optional[WindowEnergyResult]:
        """
        Pre-task = total_point_delta - task_sum - post_task_sum.
        Requires both point reads (rapl_before_uj and rapl_after_uj).
        Called after resolve_post_task() so post_task_sum is passed in via caller.
        """
        # Note: caller must pass rapl_after_uj and post_task_raw via
        # SpbmV2Resolver.resolve_pre_task_with_context() below.
        # This base method is a no-op — use resolve_pre_task_with_context().
        return None

    def resolve_pre_task_with_context(
        self,
        cursor: sqlite3.Cursor,
        run_id: int,
        rapl_before_uj: Optional[int],
        rapl_after_uj: Optional[int],
        post_task_raw_uj: int,
        pre_task_duration_sec: float,
    ) -> Optional[WindowEnergyResult]:
        """
        Pre-task with full context: residual after task and post-task are known.

        Formula: pre_raw = (rapl_after - rapl_before) - task_sum - post_task_raw

        Args:
            rapl_after_uj:    Cumulative pkg at t2 (after post-task).
            post_task_raw_uj: Already-computed post-task raw energy.
        """
        if rapl_before_uj is None or rapl_after_uj is None:
            return None

        # task_sum = SUM of all samples for this run on PACKAGE domain.
        cursor.execute("""
            SELECT COALESCE(SUM(esd.energy_uj), 0)
            FROM energy_sample_domains esd
            WHERE esd.run_id = ? AND esd.domain_id = ?
        """, (run_id, self._pkg_domain_id))
        task_sum_uj = int(cursor.fetchone()[0] or 0)

        total_delta = rapl_after_uj - rapl_before_uj
        raw_uj = max(0, total_delta - task_sum_uj - post_task_raw_uj)
        dur_ns = int(pre_task_duration_sec * 1_000_000_000)

        return WindowEnergyResult(
            raw_uj=raw_uj,
            attributed_uj=raw_uj,
            method="MEASURED_DELTA",
            duration_ns=dur_ns,
        )

    def resolve_post_task(
        self,
        cursor: sqlite3.Cursor,
        run_id: int,
        rapl_after_uj: Optional[int],
        t1_ns: int,
        post_task_duration_sec: float,
        cpu_frac_post: float,
    ) -> Optional[WindowEnergyResult]:
        """
        Post-task = SUM of domain deltas for samples after t1.
        On GN100 sampling stops at stop_measurement() — typically 0 or 1 sample.
        """
        cursor.execute("""
            SELECT COALESCE(SUM(esd.energy_uj), 0)
            FROM energy_sample_domains esd
            JOIN energy_samples_v2 esv2 ON esv2.sample_id = esd.sample_id
            WHERE esd.run_id = ?
              AND esd.domain_id = ?
              AND esv2.timestamp_ns > ?
        """, (run_id, self._pkg_domain_id, t1_ns))
        raw_uj = int(cursor.fetchone()[0] or 0)
        dur_ns = int(post_task_duration_sec * 1_000_000_000)

        return WindowEnergyResult(
            raw_uj=raw_uj,
            attributed_uj=raw_uj,
            method="MEASURED_SUM",
            duration_ns=dur_ns,
        )

    def name(self) -> str:
        return f"SpbmV2Resolver(domain={self._pkg_domain_id})"


# ---------------------------------------------------------------------------
# IokitV2Resolver — Apple Silicon (energy_sample_domains, no point reads)
# ---------------------------------------------------------------------------

class IokitV2Resolver(EnergyWindowResolverABC):
    """
    Apple Silicon IOKit energy via energy_sample_domains.

    IOKit does not support point reads (read_energy() returns None).
    Pre/post task windows have no samples (sampling covers [t0, t1] only).

    Strategy: extrapolate from task avg power * window duration.
    Methodology: INFERRED — documented as lower confidence than MEASURED.

    Primary domain: CPU_APPLE (domain_id=12, child of UNIFIED).
    """

    def __init__(self, pkg_domain_id: int) -> None:
        """
        Args:
            pkg_domain_id: energy_domains.domain_id for CPU_APPLE or equivalent.
        """
        self._pkg_domain_id = pkg_domain_id

    def _task_avg_power_watts(
        self,
        cursor: sqlite3.Cursor,
        run_id: int,
        t0_ns: int,
        t1_ns: int,
    ) -> float:
        """
        Compute average power from task samples.

        Args:
            t0_ns: task start timestamp (ns).
            t1_ns: task end timestamp (ns).

        Returns:
            Average power in watts, or 0.0 if no samples.
        """
        cursor.execute("""
            SELECT COALESCE(SUM(esd.energy_uj), 0)
            FROM energy_sample_domains esd
            WHERE esd.run_id = ? AND esd.domain_id = ?
        """, (run_id, self._pkg_domain_id))
        task_sum_uj = float(cursor.fetchone()[0] or 0)

        task_dur_s = (t1_ns - t0_ns) / 1e9 if t1_ns > t0_ns else 1.0
        # Convert µJ to J, divide by seconds to get watts.
        return task_sum_uj / 1_000_000 / task_dur_s

    def resolve_pre_task(
        self,
        cursor: sqlite3.Cursor,
        run_id: int,
        rapl_before_uj: Optional[int],
        pre_task_duration_sec: float,
        cpu_frac_pre: float,
    ) -> Optional[WindowEnergyResult]:
        """
        Pre-task via power extrapolation.
        Requires start_time_ns and task_duration_ns from runs table.
        Returns None if timestamps unavailable.
        """
        cursor.execute(
            "SELECT start_time_ns, task_duration_ns FROM runs WHERE run_id = ?",
            (run_id,)
        )
        row = cursor.fetchone()
        if not row or not row[0] or not row[1]:
            return None

        t0_ns = int(row[0])
        t1_ns = t0_ns + int(row[1])
        avg_power_w = self._task_avg_power_watts(cursor, run_id, t0_ns, t1_ns)

        raw_uj = int(avg_power_w * pre_task_duration_sec * 1_000_000)
        dur_ns = int(pre_task_duration_sec * 1_000_000_000)

        logger.debug(
            "Run %d: IOKit pre-task extrapolated %dµJ from avg_power=%.3fW",
            run_id, raw_uj, avg_power_w,
        )
        return WindowEnergyResult(
            raw_uj=raw_uj,
            attributed_uj=raw_uj,
            method="INFERRED_POWER",
            duration_ns=dur_ns,
        )

    def resolve_post_task(
        self,
        cursor: sqlite3.Cursor,
        run_id: int,
        rapl_after_uj: Optional[int],
        t1_ns: int,
        post_task_duration_sec: float,
        cpu_frac_post: float,
    ) -> Optional[WindowEnergyResult]:
        """
        Post-task via power extrapolation.
        Same avg_power used as pre-task — IOKit has no post-task samples.
        """
        cursor.execute(
            "SELECT start_time_ns, task_duration_ns FROM runs WHERE run_id = ?",
            (run_id,)
        )
        row = cursor.fetchone()
        if not row or not row[0] or not row[1]:
            return None

        t0_ns = int(row[0])
        avg_power_w = self._task_avg_power_watts(cursor, run_id, t0_ns, t1_ns)

        raw_uj = int(avg_power_w * post_task_duration_sec * 1_000_000)
        dur_ns = int(post_task_duration_sec * 1_000_000_000)

        logger.debug(
            "Run %d: IOKit post-task extrapolated %dµJ from avg_power=%.3fW",
            run_id, raw_uj, avg_power_w,
        )
        return WindowEnergyResult(
            raw_uj=raw_uj,
            attributed_uj=raw_uj,
            method="INFERRED_POWER",
            duration_ns=dur_ns,
        )

    def name(self) -> str:
        return f"IokitV2Resolver(domain={self._pkg_domain_id})"


# ---------------------------------------------------------------------------
# Factory — detects platform and returns correct resolver
# ---------------------------------------------------------------------------

class EnergyWindowResolverFactory:
    """
    Detects platform energy storage format and returns the correct resolver.

    Detection order:
      1. Legacy energy_samples present → RaplLegacyResolver (x86 RAPL/AMD)
      2. V2 samples with root domain (parent IS NULL) → SpbmV2Resolver (GN100)
      3. V2 samples with child domain (parent IS NOT NULL) → IokitV2Resolver (Mac)
      4. No samples → NullResolver

    Adding a new platform: add detection logic before step 4.
    PAC-2: all platform detection lives here, never in callers.
    """

    @staticmethod
    def _find_pkg_domain(
        cursor: sqlite3.Cursor,
        run_id: int,
        require_root: bool,
    ) -> Optional[int]:
        """
        Find the primary energy domain_id for a run in energy_sample_domains.

        Args:
            require_root: if True, only consider domains with no parent
                         (parent_domain_id IS NULL). If False, walk to children.

        Returns:
            domain_id with highest total energy, or None if no samples.
        """
        if require_root:
            cursor.execute("""
                SELECT esd.domain_id
                FROM energy_sample_domains esd
                JOIN energy_domains ed ON ed.domain_id = esd.domain_id
                WHERE esd.run_id = ?
                  AND ed.parent_domain_id IS NULL
                  AND ed.is_cumulative = 1
                GROUP BY esd.domain_id
                ORDER BY SUM(esd.energy_uj) DESC
                LIMIT 1
            """, (run_id,))
        else:
            # Walk one level down — for platforms where root has no samples
            # but its children do (Apple: UNIFIED root → CPU_APPLE child).
            cursor.execute("""
                SELECT esd.domain_id
                FROM energy_sample_domains esd
                JOIN energy_domains ed ON ed.domain_id = esd.domain_id
                JOIN energy_domains parent
                    ON parent.domain_id = ed.parent_domain_id
                WHERE esd.run_id = ?
                  AND parent.parent_domain_id IS NULL
                  AND ed.is_cumulative = 1
                GROUP BY esd.domain_id
                ORDER BY SUM(esd.energy_uj) DESC
                LIMIT 1
            """, (run_id,))
        row = cursor.fetchone()
        return int(row[0]) if row else None

    @classmethod
    def detect(
        cls,
        cursor: sqlite3.Cursor,
        run_id: int,
        has_point_reads: bool,
    ) -> EnergyWindowResolverABC:
        """
        Detect platform and return the appropriate resolver.

        Args:
            cursor:          Open DB cursor.
            run_id:          Target run.
            has_point_reads: True if rapl_before_pretask dict was non-None
                             (i.e. the platform supports read_energy() point reads).

        Returns:
            Concrete EnergyWindowResolverABC implementation. Never None.
        """
        # Step 1: legacy energy_samples (x86 RAPL, AMD RAPL)
        cursor.execute(
            "SELECT COUNT(*) FROM energy_samples WHERE run_id = ?",
            (run_id,)
        )
        legacy_count = int(cursor.fetchone()[0] or 0)
        if legacy_count > 0:
            logger.debug("Run %d: detected RaplLegacyResolver (%d legacy samples)",
                         run_id, legacy_count)
            return RaplLegacyResolver()

        # Step 2: v2 samples — check for root domain (SPBM/GN100, AMD v2)
        pkg_domain_id = cls._find_pkg_domain(cursor, run_id, require_root=True)
        if pkg_domain_id is not None:
            # Root domain has samples — SPBM/GN100 or AMD v2.
            logger.debug("Run %d: detected SpbmV2Resolver (domain=%d point_reads=%s)",
                         run_id, pkg_domain_id, has_point_reads)
            return SpbmV2Resolver(pkg_domain_id)

        # Step 3: no root domain samples — walk to children.
        # Apple IOKit: UNIFIED root has no samples, CPU_APPLE child does.
        child_domain_id = cls._find_pkg_domain(cursor, run_id, require_root=False)
        if child_domain_id is not None:
            logger.debug("Run %d: detected IokitV2Resolver (domain=%d)",
                         run_id, child_domain_id)
            return IokitV2Resolver(child_domain_id)

        # Step 4: no samples at all
        logger.debug("Run %d: no energy samples — NullResolver", run_id)
        return NullResolver()
