"""
================================================================================
ENGINE BACKED COLLECTOR  —  core/telemetry/engine_backed_collector.py
================================================================================

PURPOSE:
    CacheTelemetryCollector that delegates to a ServingEngineAdapter to
    produce StateReuseEvent / CacheStateSnapshot / ServingRuntimeSnapshot
    objects which GEM inserts into the DB.

    Bridges B3 (adapter interface) into B2 (collector contract).

    Reads capabilities() to decide what to collect and where to write:

        caps.kv_cache_metrics       → cache_state_snapshots (B2 table)
        caps.kv_cache_metrics       → serving_runtime_snapshots snapshot_type='kv_cache'
        caps.expert_tier_metrics    → serving_runtime_snapshots snapshot_type='expert_tier'
        caps.queue_metrics          → serving_runtime_snapshots snapshot_type='queue'
        caps.token_metrics          → serving_runtime_snapshots snapshot_type='token_rate'
        caps.request_correlation    → state_reuse_events (B2 table)

SCHEMA BOUNDARY (review 2026-09-22 point 2):
    Expert tier data (Colibri) MUST NOT go into cache_state_snapshots.
    It goes into serving_runtime_snapshots with snapshot_type='expert_tier'.
    This is enforced here, not in SQL.

B2.4 ENFORCEMENT:
    state_reuse_events rows produced ONLY when:
        caps.request_correlation is True
        AND recovery_id is not None
        AND engine returned a non-None prefix_cache_hit

INV-2: collect() returns data objects, never writes DB.
       The runner inserts the returned rows.

AUTHOR: Deepak Panigrahy
================================================================================
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from core.telemetry.cache_collector import (
    CacheTelemetryCollector,
    CacheTelemetryRegistry,
    StateReuseEvent,
    CacheStateSnapshot,
)
from core.serving.serving_adapter import ServingEngineAdapter

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# New data transfer object for serving_runtime_snapshots (v110).
# Returned by collect() alongside the B2 objects.
# Runner inserts these into serving_runtime_snapshots.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ServingRuntimeSnapshot:
    """
    One row in serving_runtime_snapshots (v110).

    snapshot_type determines which nullable columns are populated.
    All other column groups must be left None for that row.
    """
    # Required
    timestamp_ns: int = 0
    engine_name: str = ""
    engine_type: str = ""
    snapshot_type: str = ""          # 'kv_cache'|'expert_tier'|'queue'|'token_rate'
    telemetry_scope: str = "unavailable"

    # Set by runner from execution context
    run_id: Optional[int] = None
    attempt_id: Optional[int] = None

    # KV cache (snapshot_type='kv_cache')
    kv_capacity_tokens: Optional[int] = None
    kv_occupied_tokens: Optional[int] = None
    kv_occupancy_fraction: Optional[float] = None
    kv_hit_rate_aggregate: Optional[float] = None
    kv_num_evictions: Optional[int] = None

    # Expert tier (snapshot_type='expert_tier') — Colibri only
    tier_vram_bytes: Optional[int] = None
    tier_ram_bytes: Optional[int] = None
    tier_disk_bytes: Optional[int] = None
    tier_vram_fraction: Optional[float] = None
    tier_ram_fraction: Optional[float] = None
    tier_disk_fraction: Optional[float] = None

    # Queue / health (snapshot_type='queue')
    queue_active: Optional[int] = None
    queue_waiting: Optional[int] = None
    queue_completed: Optional[int] = None
    queue_rejected: Optional[int] = None

    # Token rate (snapshot_type='token_rate')
    tokens_per_second: Optional[float] = None
    ttft_ms: Optional[float] = None
    prompt_tokens_total: Optional[int] = None
    generation_tokens_total: Optional[int] = None

    # Engine-specific overflow
    extra_json: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Collector
# ─────────────────────────────────────────────────────────────────────────────

class EngineBackedCollector(CacheTelemetryCollector):
    """
    CacheTelemetryCollector backed by a ServingEngineAdapter.

    Returns three lists: B2 state_reuse_events, B2 cache_state_snapshots,
    and v110 serving_runtime_snapshots. The runner inserts all three.

    For RemoteAPIAdapter all three lists are empty — identical to
    NoOpCollector behaviour (B2.5).

    COLLECTOR_ID = 'engine_backed'
    """

    COLLECTOR_ID = "engine_backed"

    def __init__(self, adapter: ServingEngineAdapter):
        self._adapter = adapter
        self._caps = adapter.capabilities()
        info = adapter.get_engine_info()
        logger.info(
            "EngineBackedCollector: %s "
            "(telemetry_scope=%s kv=%s expert_tier=%s "
            "queue=%s token=%s prometheus=%s request_corr=%s)",
            info.engine_name,
            self._caps.telemetry_scope,
            self._caps.kv_cache_metrics,
            self._caps.expert_tier_metrics,
            self._caps.queue_metrics,
            self._caps.token_metrics,
            self._caps.prometheus_metrics,
            self._caps.request_correlation,
        )

    def collect(
        self,
        run_id: int,
        attempt_id: int,
        recovery_id: Optional[int],
        request_context: dict,
    ) -> tuple[
        list[StateReuseEvent],
        list[CacheStateSnapshot],
        list[ServingRuntimeSnapshot],
    ]:
        """
        Collect all available telemetry for a completed recovery.

        Returns a 3-tuple. The standard B2 contract returns a 2-tuple,
        so this method extends the return signature. The runner must
        handle the third list (serving_runtime_snapshots).

        Never raises — returns ([], [], []) on any error.
        """
        reuse_events: list[StateReuseEvent] = []
        b2_snapshots: list[CacheStateSnapshot] = []
        rt_snapshots: list[ServingRuntimeSnapshot] = []

        now_ns = time.time_ns()
        scope = self._caps.telemetry_scope

        try:
            # ── 1. KV cache ────────────────────────────────────────────────
            if self._caps.kv_cache_metrics:
                cache = self._adapter.get_cache_state()

                if cache.capacity_tokens > 0:
                    # B2 table (backward compat — keep populating it)
                    b2_snapshots.append(CacheStateSnapshot(
                        run_id=run_id,
                        attempt_id=attempt_id,
                        timestamp_ns=now_ns,
                        engine_name=self._adapter.get_name(),
                        cache_type=cache.cache_type or "kv",
                        capacity_tokens=cache.capacity_tokens,
                        occupied_tokens=cache.occupied_tokens,
                        occupancy_fraction=cache.occupancy_fraction,
                        hit_rate_aggregate=cache.hit_rate_aggregate,
                    ))

                    # v110 table — typed row
                    rt_snapshots.append(ServingRuntimeSnapshot(
                        timestamp_ns=now_ns,
                        engine_name=self._adapter.get_name(),
                        engine_type=self._adapter.ENGINE_TYPE,
                        snapshot_type="kv_cache",
                        telemetry_scope=scope,
                        run_id=run_id,
                        attempt_id=attempt_id,
                        kv_capacity_tokens=cache.capacity_tokens,
                        kv_occupied_tokens=cache.occupied_tokens,
                        kv_occupancy_fraction=cache.occupancy_fraction,
                        kv_hit_rate_aggregate=cache.hit_rate_aggregate,
                        kv_num_evictions=cache.num_evictions or None,
                    ))

            # ── 2. Expert tier — Colibri only, NOT KV cache ────────────────
            if self._caps.expert_tier_metrics:
                tier = self._adapter.get_expert_tier_state()

                # Only write if at least one tier has data.
                if tier.vram_bytes or tier.ram_bytes or tier.disk_bytes:
                    rt_snapshots.append(ServingRuntimeSnapshot(
                        timestamp_ns=now_ns,
                        engine_name=self._adapter.get_name(),
                        engine_type=self._adapter.ENGINE_TYPE,
                        snapshot_type="expert_tier",
                        telemetry_scope=scope,
                        run_id=run_id,
                        attempt_id=attempt_id,
                        tier_vram_bytes=tier.vram_bytes or None,
                        tier_ram_bytes=tier.ram_bytes or None,
                        tier_disk_bytes=tier.disk_bytes or None,
                        tier_vram_fraction=tier.vram_fraction or None,
                        tier_ram_fraction=tier.ram_fraction or None,
                        tier_disk_fraction=tier.disk_fraction or None,
                    ))

            # ── 3. Queue / health ─────────────────────────────────────────
            if self._caps.queue_metrics:
                q = self._adapter.get_queue_state()

                if q.active is not None or q.waiting is not None:
                    rt_snapshots.append(ServingRuntimeSnapshot(
                        timestamp_ns=now_ns,
                        engine_name=self._adapter.get_name(),
                        engine_type=self._adapter.ENGINE_TYPE,
                        snapshot_type="queue",
                        telemetry_scope=scope,
                        run_id=run_id,
                        attempt_id=attempt_id,
                        queue_active=q.active,
                        queue_waiting=q.waiting,
                        queue_completed=q.completed,
                        queue_rejected=q.rejected,
                    ))

            # ── 4. Per-request reuse event (B2.4 gated) ───────────────────
            # Only when engine supports request-level correlation AND
            # we have a recovery_id to FK against.
            if (
                self._caps.request_correlation
                and recovery_id is not None
            ):
                request_id = request_context.get("request_id")
                req = self._adapter.get_request_metrics(
                    request_id=request_id
                )

                if req.prefix_cache_hit is not None:
                    reuse_fraction = None
                    if req.prompt_tokens > 0:
                        reuse_fraction = (
                            req.prefix_cache_hit_tokens / req.prompt_tokens
                        )

                    reuse_events.append(StateReuseEvent(
                        reuse_type="prefix_cache",
                        recovery_id=recovery_id,
                        attempt_id=attempt_id,
                        run_id=run_id,
                        reuse_source=self._adapter.get_name(),
                        tokens_reused=req.prefix_cache_hit_tokens or None,
                        tokens_recomputed=(
                            req.prompt_tokens - req.prefix_cache_hit_tokens
                            if req.prompt_tokens > 0 else None
                        ),
                        reuse_fraction=reuse_fraction,
                        cache_hit=1 if req.prefix_cache_hit else 0,
                        cache_query_time_ns=None,
                        state_size_bytes=None,
                    ))

        except Exception as exc:
            logger.warning(
                "EngineBackedCollector.collect: error "
                "(attempt_id=%s recovery_id=%s): %s. "
                "Returning empty lists — experiment continues.",
                attempt_id, recovery_id, exc,
                exc_info=True,
            )
            return ([], [], [])

        return (reuse_events, b2_snapshots, rt_snapshots)

    def get_name(self) -> str:
        return f"engine_backed({self._adapter.get_name()})"


# Self-register into CacheTelemetryRegistry on import.
CacheTelemetryRegistry.register(EngineBackedCollector)
