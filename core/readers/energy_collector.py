"""
core/readers/energy_collector.py

EnergyCollector — generic energy collection loop for any hardware platform.

Replaces BOTH _sampling_loop in energy_engine.py AND SPBMSampler in
spbm_energy_reader.py. One implementation, all platforms, forever.

Design: ports and adapters.
    Port:    EnergyReaderABC (hardware reading interface)
    Schema:  MeasurementSchema (declarative platform capabilities)
    Sample:  EnergySample (canonical in-memory measurement)
    Adapter: PersistenceAdapter (storage interface)

CRITICAL: This class MUST NOT contain any platform-specific branching.
If you find yourself writing 'if source == "RAPL"' in this class, STOP.
That logic belongs in the reader (get_measurement_schema) or in an adapter.

The only semantic branch allowed is domain_type (ENERGY_COUNTER vs POWER_RAIL)
which is a physical measurement type, not a platform identity.

PAC-2 compliant: sampling thread errors logged and continued, never crash.
NFR DC-1: 30% inline comment coverage maintained throughout.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, List, Optional, TYPE_CHECKING

from core.readers.energy_sample import EnergySample
from core.readers.measurement_schema import (
    DomainDescriptor,
    MeasurementSchema,
    ENERGY_COUNTER,
    POWER_RAIL,
)
from core.readers.persistence_adapter import PersistenceAdapter

if TYPE_CHECKING:
    from core.readers.interfaces import EnergyReaderABC

logger = logging.getLogger(__name__)


class EnergyCollector:
    """
    Generic energy collection loop for any hardware platform.

    Reads energy counters at the rate specified by MeasurementSchema.sampling_hz.
    Computes deltas per tick with counter wraparound correction.
    Emits EnergySample objects to all registered PersistenceAdapters.

    Usage:
        collector = EnergyCollector(reader, adapters, run_id, source_id)
        collector.start()
        # ... experiment runs ...
        collector.stop()
    """

    def __init__(
        self,
        reader,                              # EnergyReaderABC implementation
        adapters: List[PersistenceAdapter],  # registered persistence adapters
        run_id: int,                         # active run — set before start()
        source_id: int,                      # energy_sources.source_id for this reader
    ):
        self._reader = reader
        self._adapters = adapters
        self._run_id = run_id
        self._source_id = source_id

        # Cache schema from reader — called once, never again
        self._schema: MeasurementSchema = reader.get_measurement_schema()

        # Build native_key -> DomainDescriptor lookup for fast per-tick access
        self._domain_map: Dict[str, DomainDescriptor] = {
            d.native_key: d for d in self._schema.domains
        }

        # Wraparound ceiling: 2^counter_width_bits µJ
        self._wrap_max: int = (1 << self._schema.counter_width_bits)

        # Sampling state
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._prev: Optional[Dict[str, Optional[int]]] = None  # previous tick readings

        logger.debug(
            "EnergyCollector init: source=%s domains=%d schema_bound_hz=%d wrap_bits=%d",
            self._schema.source,
            len(self._schema.domains),
            self._schema.sampling_hz,
            self._schema.counter_width_bits,
        )

    def start(self) -> None:
        """
        Read initial counter values and start background sampling thread.
        Initial read establishes the baseline for first delta computation.
        """
        if not self._schema.domains:
            # Empty schema (DummyReader etc) — no-op, nothing to collect
            logger.debug(
                "EnergyCollector.start: empty schema for %s — no-op",
                self._schema.source,
            )
            return

        # B13: rate decision (and the one time capability probe, about 1.5 s on a new
        # machine) BEFORE the baseline read, so the first tick never spans the probe.
        from core.readers.sampling_policy import describe
        rates = describe(self._schema, self._reader)   # cached after first use
        # Baseline read before thread starts — prevents first-tick spike
        self._prev = self._reader.read_energy()
        # B13: reset per run state. A reused collector kept _prev_tick_ns from the
        # previous run, so the first tick integrated rail power over the gap between runs.
        self._prev_tick_ns = time.time_ns()
        self._stats = {"ticks": 0, "emitted": 0, "empty": 0, "errors": 0,
                       "max_gap_ns": 0, "late_join": False}
        self._running = True

        # B13: one policy for all samplers: min(config, probed for this machine, schema bound)
        self._stats.update(rates)
        self._effective_hz = rates["effective_hz"]
        interval_s = 1.0 / self._effective_hz  # seconds between ticks

        self._thread = threading.Thread(
            target=self._loop,
            args=(interval_s,),
            daemon=True,
            name=f"EnergyCollector-{self._schema.source}",
        )
        self._thread.start()
        logger.info(
            "EnergyCollector started: source=%s effective_hz=%.1f (schema bound %s)",
            self._schema.source, self._effective_hz, self._schema.sampling_hz,
        )

    def stop(self) -> None:
        """
        Stop background sampling, join thread, flush all adapters.
        Flush must happen AFTER thread joins to avoid concurrent writes.
        """
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                # B13: thread still writing after flush would lose samples silently
                self._stats["late_join"] = True
                logger.error("EnergyCollector: thread did not stop within 5 s: source=%s",
                             self._schema.source)
        # B13: final tick so the energy between the last tick and stop is kept
        if self._schema.domains and not self._stats.get("late_join"):
            self._tick(final=True)
        # B13: achieved rate from emitted samples over the sampled span
        if self._stats.get("emitted", 0) > 1 and getattr(self, "_first_emit_ns", None):
            span_s = (self._prev_tick_ns - self._first_emit_ns) / 1e9
            self._stats["achieved_hz"] = (self._stats["emitted"] - 1) / span_s if span_s > 0 else None
        self._stats["source"] = self._schema.source
        logger.info("EnergyCollector stats: source=%s %s", self._schema.source, self._stats)

        # Flush all adapters — both return buffered sample lists, no DB writes
        # Route by is_legacy flag — avoids circular import of writer classes
        self._flushed_v2_samples: List = []
        self._flushed_legacy_samples: List = []
        for adapter in self._adapters:
            try:
                result = adapter.flush()
                if isinstance(result, list):
                    if getattr(adapter, "is_legacy", False):
                        self._flushed_legacy_samples.extend(result)
                    else:
                        self._flushed_v2_samples.extend(result)
            except Exception as e:
                logger.warning("EnergyCollector: adapter flush failed: %s", e)

        logger.info(
            "EnergyCollector stopped: source=%s", self._schema.source
        )

    def _loop(self, interval_s: float) -> None:
        """
        Core sampling loop. Platform-agnostic. Iterates schema domains only.

        This method MUST contain zero platform-specific branching.
        All platform differences are expressed through MeasurementSchema.
        """
        next_tick = time.monotonic() + interval_s

        while self._running:
            # Sleep until next scheduled tick
            now = time.monotonic()
            sleep_s = next_tick - now
            if sleep_s > 0:
                time.sleep(sleep_s)
            next_tick += interval_s

            if not self._tick():
                time.sleep(0.01)    # brief pause to avoid tight error loop

    def _tick(self, final: bool = False) -> bool:
        """One sample. Returns False on error. Never raises (PAC-2).

        B13: every tick is counted; an empty or failed tick is counted and the gap
        to the previous emitted sample is tracked, so loss is visible, never silent.
        """
        self._stats["ticks"] += 1
        try:
            curr = self._reader.read_energy()  # native key dict
            tick_end_ns = time.time_ns()
            interval_ns = tick_end_ns - self._prev_tick_ns

            domains: Dict[str, int] = {}
            for native_key, descriptor in self._domain_map.items():
                c = curr.get(native_key) if curr else None
                p = self._prev.get(native_key) if self._prev else None
                if descriptor.domain_type == ENERGY_COUNTER:
                    if c is not None and p is not None:
                        delta = c - p
                        if delta < 0:
                            delta += self._wrap_max
                        domains[descriptor.canonical_name] = delta
                    # absent means not measured; never substitute zero
                elif descriptor.domain_type == POWER_RAIL:
                    if c is not None:
                        domains[descriptor.canonical_name] = int(c * interval_ns / 1_000_000)  # mW * ns / 1e6 = uJ

            if not domains:
                # B13: reader returned nothing. Keep _prev and _prev_tick_ns so the next
                # counter delta and the next rail interval span this tick: energy is kept.
                self._stats["empty"] += 1
                return True

            sample = EnergySample(
                timestamp_ns=tick_end_ns,
                interval_ns=interval_ns,
                source=self._schema.source,
                run_id=self._run_id,
                domains=domains,
                raw_start=dict(self._prev) if self._prev else {},
                raw_end=dict(curr) if curr else {},
            )
            for adapter in self._adapters:
                adapter.write(sample)   # PAC-2: adapter.write never raises
            self._stats["emitted"] += 1
            if self._stats["emitted"] == 1:
                self._first_emit_ns = tick_end_ns
            self._stats["max_gap_ns"] = max(self._stats["max_gap_ns"], interval_ns)
            self._prev = curr
            self._prev_tick_ns = tick_end_ns
            return True
        except Exception as e:
            # PAC-2: never crash the sampling thread; counter energy is kept by the next delta
            self._stats["errors"] += 1
            logger.error("EnergyCollector._tick error (final=%s): %s", final, e)
            return False

    @property
    def schema(self) -> MeasurementSchema:
        """Expose schema for inspection by energy_engine diagnostics."""
        return self._schema
