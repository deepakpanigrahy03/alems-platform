#!/usr/bin/env python3
"""
================================================================================
SYNTHETIC ENERGY READER  —  core/readers/synthetic_energy_reader.py
================================================================================

Purpose:
    A deterministic energy reader that returns fixed, configurable values
    instead of reading physical hardware counters.
    Used exclusively when ALEMS_PLATFORM_OVERRIDE=synthetic is set.

    This enables the full A-LEMS measurement pipeline — registration,
    selection, EnergyCollector, DB writes, baseline subtraction — to run
    on any machine without physical energy measurement hardware.

Why this exists:
    Every other reader in A-LEMS requires specific hardware:
        RAPLReader     → Intel/AMD x86 with RAPL sysfs
        SPBMEnergyReader → NVIDIA Grace GN100
        IOKitPowerReader → Apple Silicon macOS
    This means CI pipelines, developer laptops, and VMs cannot run
    the measurement pipeline at all without faking hardware.

    SyntheticEnergyReader solves this by being a real EnergyReaderABC
    implementation that participates in the full registry + selection path
    but returns deterministic values from a YAML fixture file instead of
    hardware counters. The rest of the platform (EnergyCollector, DB, harness)
    is completely unaware that the values are synthetic.

Three modes:
    constant — same values every call. Used for CI assertions.
               energy_uj in the DB will always equal fixture values.
    linear   — monotonically increasing by a fixed increment per call.
               Used for testing delta computation and baseline subtraction.
    replay   — reads from a CSV, one row per call, wraps at end.
               Used for testing analysis code against realistic traces.

METHOD_CONFIDENCE = 0.0:
    This is not a contradiction with is_available() returning True.
    METHOD_CONFIDENCE reflects physical measurement validity — synthetic
    values are not physically measured, so confidence is 0.0.
    is_available() reflects whether the reader can function — it can,
    always, on any machine where ALEMS_PLATFORM_OVERRIDE=synthetic is set.
    Both are correct simultaneously.

AC-6 guarantee:
    can_handle() returns True ONLY when caps.platform_class == "synthetic".
    On real hardware (GN100, x86, macOS) this always returns False and
    the reader is never selected by the registry.

Author: Deepak Panigrahy
Spec:   SPEC 35C, Part B
================================================================================
"""

import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from core.readers.interfaces import EnergyReaderABC
from core.models.normalized_energy_reading import NormalizedEnergyReading
from core.readers.measurement_schema import (
    MeasurementSchema,
    DomainDescriptor,
    ENERGY_COUNTER,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default fixture path — relative to project root
# ---------------------------------------------------------------------------
_DEFAULT_FIXTURE = Path("data/fixtures/synthetic_energy.yaml")


class SyntheticEnergyReader(EnergyReaderABC):
    """
    Deterministic energy reader for synthetic platform testing.

    Inherits EnergyReaderABC and satisfies the full interface contract.
    Participates in registry-based selection the same way real readers do.
    Selected only when caps.platform_class == "synthetic".

    Configuration (config/app_settings.yaml):
        plugins:
          synthetic:
            mode: constant          # constant | linear | replay
            package_uj: 1000000
            core_uj: 600000
            dram_uj: 200000
            increment_uj: 50000     # linear mode only
            csv_path: ...           # replay mode only

    When no config is present, values from data/fixtures/synthetic_energy.yaml
    are used as defaults.
    """

    # ------------------------------------------------------------------
    # Methodology attributes — required by EnergyReaderABC contract
    # ------------------------------------------------------------------
    METHOD_ID          = "synthetic_energy"
    METHOD_NAME        = "Synthetic Energy Reader (CI/Test)"
    METHOD_LAYER       = "silicon"
    METHOD_CONFIDENCE  = 0.0   # not physically measured — see module docstring
    METHOD_PROVENANCE  = "MEASURED"   # pipeline treats it as measured (intentional)
    METHOD_PARAMS      = {"source": "yaml_fixture", "stub": True}
    FALLBACK_METHOD_ID = None

    # ------------------------------------------------------------------
    # Registry contract (SPEC 35A)
    # ------------------------------------------------------------------
    PRIORITY: int = 100   # primary tier — wins when synthetic platform active

    @classmethod
    def can_handle(cls, caps) -> bool:
        """
        Eligible ONLY when platform_class is 'synthetic'.

        This is the AC-6 guarantee: on real hardware (GN100, x86, macOS)
        platform_class is never 'synthetic', so this reader is never
        selected outside of explicit synthetic override sessions.

        Args:
            caps: PlatformCapabilities from core/utils/platform.py.

        Returns:
            True only when caps.platform_class == "synthetic".
        """
        return getattr(caps, "platform_class", "") == "synthetic"

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def __init__(self, config: dict = None):
        """
        Initialise synthetic reader from fixture file and optional config.

        Config resolution order (first found wins):
            1. config dict passed directly (from factory/tests)
            2. config/app_settings.yaml [plugins.synthetic] section
            3. data/fixtures/synthetic_energy.yaml defaults

        Args:
            config: hw_config dict (accepted for API consistency; used
                    to look up plugins.synthetic if present).
        """
        self._config = config or {}

        # Load fixture defaults first
        fixture = self._load_fixture()

        # Resolve operating mode
        self._mode = self._resolve("mode", fixture, default="constant")

        # Resolve constant mode values
        self._package_uj = int(self._resolve("package_uj", fixture, default=1_000_000))
        self._core_uj    = int(self._resolve("core_uj",    fixture, default=600_000))
        self._dram_uj    = int(self._resolve("dram_uj",    fixture, default=200_000))

        # Resolve linear mode increment
        self._increment  = int(self._resolve("increment_uj", fixture, default=50_000))

        # Replay mode CSV path
        self._csv_path   = self._resolve("csv_path", fixture, default=None)

        # Internal state for linear and replay modes
        self._call_count  = 0
        self._replay_rows: List[Dict] = []
        self._replay_idx  = 0

        if self._mode == "replay" and self._csv_path:
            self._load_replay_csv()

        logger.info(
            "SyntheticEnergyReader: mode=%s package_uj=%s core_uj=%s dram_uj=%s",
            self._mode, self._package_uj, self._core_uj, self._dram_uj,
        )

    # ------------------------------------------------------------------
    # EnergyReaderABC interface
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """
        Always True on synthetic platform.

        The reader can always function when ALEMS_PLATFORM_OVERRIDE=synthetic
        is set. No hardware access is required.

        Returns:
            True always.
        """
        return True

    def get_name(self) -> str:
        """Return reader name for logging and platform summary."""
        return f"SyntheticEnergyReader(mode={self._mode})"

    def read_normalized(self) -> NormalizedEnergyReading:
        """
        Return a NormalizedEnergyReading from fixture values.

        In constant mode: same values every call.
        In linear mode:   values increase by increment_uj per call.
        In replay mode:   values from CSV, one row per call, wraps.

        Returns:
            NormalizedEnergyReading with synthetic energy values.
        """
        self._call_count += 1

        if self._mode == "linear":
            offset = (self._call_count - 1) * self._increment
            pkg  = self._package_uj + offset
            cpu  = self._core_uj    + offset
            dram = self._dram_uj    + offset
        elif self._mode == "replay" and self._replay_rows:
            row  = self._replay_rows[self._replay_idx % len(self._replay_rows)]
            self._replay_idx += 1
            pkg  = row.get("package_uj", self._package_uj)
            cpu  = row.get("core_uj",    self._core_uj)
            dram = row.get("dram_uj",    self._dram_uj)
        else:
            # constant mode (default)
            pkg  = self._package_uj
            cpu  = self._core_uj
            dram = self._dram_uj

        return NormalizedEnergyReading(
            pkg_uj  = pkg,
            cpu_uj  = cpu,
            gpu_uj  = None,   # synthetic platform has no GPU domain
            dram_uj = dram,
            ts_ns   = time.monotonic_ns(),
        )

    def read_energy_uj(self) -> Dict[str, int]:
        """
        Return raw domain values as dict.

        Used by legacy code paths that call read_energy_uj() directly
        rather than read_normalized(). Returns same values as
        read_normalized() but in dict form.

        Returns:
            Dict mapping domain name to energy in µJ.
        """
        reading = self.read_normalized()
        return {
            "package-0": reading.pkg_uj  or 0,
            "core":      reading.cpu_uj  or 0,
            "dram":      reading.dram_uj or 0,
        }

    def get_domains(self) -> List[str]:
        """
        Return list of domain names this reader provides.

        Returns:
            ["package-0", "core", "dram"] — mirrors RAPL domain names
            so the rest of the pipeline handles synthetic data identically
            to real x86 RAPL data.
        """
        return ["package-0", "core", "dram"]

    def get_measurement_schema(self) -> MeasurementSchema:
        """
        Return MeasurementSchema describing synthetic platform capabilities.

        Uses ENERGY_COUNTER domain type to match real reader schemas.
        counter_width_bits=64 (no wraparound concern with fixture values).

        Returns:
            MeasurementSchema for the synthetic platform.
        """
        return MeasurementSchema(
            source="SYNTHETIC",
            domains=(
                DomainDescriptor(
                    native_key="package-0",
                    canonical_name="PACKAGE",
                    domain_type=ENERGY_COUNTER,
                    parent_domain=None,
                ),
                DomainDescriptor(
                    native_key="core",
                    canonical_name="CORE",
                    domain_type=ENERGY_COUNTER,
                    parent_domain="PACKAGE",
                ),
                DomainDescriptor(
                    native_key="dram",
                    canonical_name="DRAM",
                    domain_type=ENERGY_COUNTER,
                    parent_domain=None,
                ),
            ),
            sampling_hz=100,
            counter_width_bits=64,
        )

    def read_energy(self) -> Dict[str, int]:
        """
        Return cumulative energy counter values.

        Matches RAPLReader.read_energy() interface used by harness.py.
        Returns same values as read_energy_uj() — synthetic counters
        are not truly cumulative but harness only uses the delta between
        two calls so this is correct.

        Returns:
            Dict mapping domain name to energy in µJ.
        """
        reading = self.read_normalized()
        return {
            "package": reading.pkg_uj  or 0,
            "core":    reading.cpu_uj  or 0,
            "dram":    reading.dram_uj or 0,
        }

    def read_energy_safe(self, max_retries: int = 3) -> Dict[str, int]:
        """
        Return energy values — never fails on synthetic platform.

        Matches RAPLReader.read_energy_safe() interface.
        retry logic is a no-op since synthetic reads never fail.

        Args:
            max_retries: Ignored on synthetic platform.

        Returns:
            Dict mapping domain name to energy in µJ.
        """
        return self.read_energy()

    def read_gpu_msr(self) -> Optional[int]:
        """
        Return None — synthetic platform has no GPU MSR.

        Returns:
            None always.
        """
        return None

    def get_method_id(self) -> str:
        """Return METHOD_ID for provenance recording."""
        return self.METHOD_ID

    def get_confidence(self) -> float:
        """
        Return METHOD_CONFIDENCE.

        0.0 because synthetic values are not physically measured.
        This is correct and intentional — see module docstring.

        Returns:
            0.0
        """
        return self.METHOD_CONFIDENCE

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_fixture(self) -> dict:
        """
        Load default fixture values from YAML file.

        Returns empty dict if file not found — caller uses hardcoded defaults.

        Returns:
            Dict of fixture values, or empty dict on error.
        """
        try:
            path = Path(_DEFAULT_FIXTURE)
            if path.exists():
                with open(path) as f:
                    return yaml.safe_load(f) or {}
        except Exception as exc:
            logger.warning(
                "SyntheticEnergyReader: could not load fixture %s: %s",
                _DEFAULT_FIXTURE, exc,
            )
        return {}

    def _resolve(self, key: str, fixture: dict, default):
        """
        Resolve a config value with priority order:
            1. plugins.synthetic section in hw_config (passed as self._config)
            2. fixture YAML constant section
            3. hardcoded default

        Args:
            key:     Config key to look up.
            fixture: Loaded fixture dict.
            default: Fallback value.

        Returns:
            Resolved value.
        """
        # Priority 1: hw_config plugins.synthetic
        plugins_cfg = self._config.get("plugins", {}).get("synthetic", {})
        if key in plugins_cfg:
            return plugins_cfg[key]

        # Priority 2: fixture constant section (top-level keys)
        if key in fixture:
            return fixture[key]

        # Priority 3: fixture constant sub-section
        constant_cfg = fixture.get("constant", {})
        if key in constant_cfg:
            return constant_cfg[key]

        return default

    def _load_replay_csv(self) -> None:
        """
        Load replay CSV into memory.

        CSV format: package_uj,core_uj,dram_uj (one row per call).
        Logs warning and falls back to constant mode if file missing.
        """
        try:
            import csv
            with open(self._csv_path) as f:
                reader = csv.DictReader(f)
                self._replay_rows = [
                    {k: int(v) for k, v in row.items()}
                    for row in reader
                ]
            logger.info(
                "SyntheticEnergyReader: loaded %d replay rows from %s",
                len(self._replay_rows), self._csv_path,
            )
        except Exception as exc:
            logger.warning(
                "SyntheticEnergyReader: replay CSV load failed: %s — "
                "falling back to constant mode", exc,
            )
            self._mode = "constant"
