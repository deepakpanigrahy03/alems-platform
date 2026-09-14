#!/usr/bin/env python3
"""
================================================================================
READER BOOTSTRAP  —  core/readers/bootstrap.py
================================================================================

Purpose:
    Register all REAL built-in reader classes into their family registries.
    Dummies are NOT registered — they are factory-level fallbacks.
    Called once from energy_engine.py before any ReaderFactory call.

    Phase 1 (this file): explicit registration via local imports.
    Phase 6 (Spec 35E):  entry_points discovery replaces this file.

    Adding a new internal reader:
        (a) Create the reader class file.
        (b) Add one import + _safe_register() call below.
        No factory.py changes needed.

Import isolation:
    Every import is local to its try/except block.
    Platform-specific readers (SPBM, IOKit) are never imported on
    machines where they would fail at import time. PAC-2 preserved.

Author: Deepak Panigrahy
Spec:   SPEC 35A, Phase 1
================================================================================
"""

import logging
from core.readers.registry import AdapterRegistry, DuplicateRegistrationError
from core.plugin_discovery import discover_plugins
from core.startup_banner import print_adapter_summary
from alems import __version__ as _CORE_VERSION
 
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Registry instances — one per real reader family.
# Typed at usage site in factory.py via AdapterRegistry[FamilyABC].
# ---------------------------------------------------------------------------

energy_registry    = AdapterRegistry(family="energy")
cpu_registry       = AdapterRegistry(family="cpu")
thermal_registry   = AdapterRegistry(family="thermal")
turbostat_registry = AdapterRegistry(family="turbostat")
msr_registry       = AdapterRegistry(family="msr")
scheduler_registry = AdapterRegistry(family="scheduler")
disk_registry      = AdapterRegistry(family="disk")

# NIC: not in registry in 35A — NICCollector handles selection internally.
# Will be added when NICReaderABC is stable across all platforms.

# ---------------------------------------------------------------------------
# _safe_register: registration that never silently hides programming errors
# ---------------------------------------------------------------------------

def _safe_register(registry: AdapterRegistry, cls) -> None:
    """
    Register cls, re-raising DuplicateRegistrationError (programming error)
    and logging all other exceptions as warnings so one broken reader
    import never prevents startup of the entire platform.

    Args:
        registry: Target AdapterRegistry instance.
        cls:      Reader class to register.
    """
    try:
        registry.register(cls)
    except DuplicateRegistrationError:
        # Two files claiming the same METHOD_ID — always fatal, always visible.
        raise
    except Exception as exc:
        logger.warning(
            "bootstrap[%s]: failed to register %s: %s — skipping",
            registry._family,
            getattr(cls, "__name__", repr(cls)),
            exc,
        )


# ---------------------------------------------------------------------------
# Per-family registration
# Each function is callable independently for targeted testing.
# Only REAL readers go in. Dummies stay in factory.py as fallbacks.
# ---------------------------------------------------------------------------

def register_energy_readers() -> None:
    """
    Register real energy readers: RAPL, SPBM, IOKit.

    Priority layout (SPEC 35A §4):
        RAPL   = 100  Linux x86_64 MEASURED
        SPBM   = 100  Linux aarch64 Grace MEASURED
        IOKit  = 100  macOS MEASURED
        (mutually exclusive by OS/hardware — no ties possible)
    """
    try:
        from core.readers.rapl_reader import RAPLReader
        _safe_register(energy_registry, RAPLReader)
    except ImportError as exc:
        logger.debug("bootstrap[energy]: RAPLReader not importable: %s", exc)

    try:
        from core.readers.spbm_energy_reader import SPBMEnergyReader
        _safe_register(energy_registry, SPBMEnergyReader)
    except ImportError as exc:
        logger.debug("bootstrap[energy]: SPBMEnergyReader not importable: %s", exc)

    try:
        from core.readers.darwin.iokit_power_reader import IOKitPowerReader
        _safe_register(energy_registry, IOKitPowerReader)
    except ImportError as exc:
        logger.debug("bootstrap[energy]: IOKitPowerReader not importable: %s", exc)

    # EnergyEstimator: INFERRED mode — still a real measurement attempt,
    # not a dummy. Returns model-estimated values, not zeros.
    try:
        from core.readers.fallback.energy_estimator import EnergyEstimator
        _safe_register(energy_registry, EnergyEstimator)
    except ImportError as exc:
        logger.debug("bootstrap[energy]: EnergyEstimator not importable: %s", exc)


def register_cpu_readers() -> None:
    """
    Register real CPU PMU readers: PerfReader, ARMPMUReader, KPerfPMUReader.

    Priority layout:
        PerfReader     = 100  Linux x86_64
        ARMPMUReader   = 100  Linux aarch64 + has_arm_pmu
        KPerfPMUReader = 100  macOS arm64
        (mutually exclusive by OS/arch)
    """
    try:
        from core.readers.perf_reader import PerfReader
        _safe_register(cpu_registry, PerfReader)
    except ImportError as exc:
        logger.debug("bootstrap[cpu]: PerfReader not importable: %s", exc)

    try:
        from core.readers.arm_pmu_reader import ARMPMUReader
        _safe_register(cpu_registry, ARMPMUReader)
    except ImportError as exc:
        logger.debug("bootstrap[cpu]: ARMPMUReader not importable: %s", exc)

    try:
        from core.readers.darwin.kperf_pmu_reader import KPerfPMUReader
        _safe_register(cpu_registry, KPerfPMUReader)
    except ImportError as exc:
        logger.debug("bootstrap[cpu]: KPerfPMUReader not importable: %s", exc)


def register_thermal_readers() -> None:
    """
    Register real thermal readers: SensorReader, ARMThermalReader, IOKitThermalReader.

    Priority layout:
        SensorReader       = 100  Linux x86_64 + has_thermal
        ARMThermalReader   = 100  Linux aarch64 + has_thermal
        IOKitThermalReader = 100  macOS
    """
    try:
        from core.readers.sensor_reader import SensorReader
        _safe_register(thermal_registry, SensorReader)
    except ImportError as exc:
        logger.debug("bootstrap[thermal]: SensorReader not importable: %s", exc)

    try:
        from core.readers.arm_thermal_reader import ARMThermalReader
        _safe_register(thermal_registry, ARMThermalReader)
    except ImportError as exc:
        logger.debug("bootstrap[thermal]: ARMThermalReader not importable: %s", exc)

    try:
        from core.readers.darwin.iokit_thermal_reader import IOKitThermalReader
        _safe_register(thermal_registry, IOKitThermalReader)
    except ImportError as exc:
        logger.debug("bootstrap[thermal]: IOKitThermalReader not importable: %s", exc)


def register_turbostat_readers() -> None:
    """
    Register real turbostat/freq readers.

    Priority layout:
        TurbostatReader       = 100  Linux x86_64
        ARMCPUFreqReader      = 100  Linux aarch64
        IOReportCPUFreqReader = 100  macOS (preferred, residency-weighted)
        DarwinCPUFreqReader   = 200  macOS (fallback, requires energy_reader arg)

    Note: DarwinCPUFreqReader needs energy_reader injected at construction.
    factory.py handles its instantiation specially — registry selects the class
    but factory.py injects the dependency.
    """
    try:
        from core.readers.turbostat_reader import TurbostatReader
        _safe_register(turbostat_registry, TurbostatReader)
    except ImportError as exc:
        logger.debug("bootstrap[turbostat]: TurbostatReader not importable: %s", exc)

    try:
        from core.readers.arm_cpufreq_reader import ARMCPUFreqReader
        _safe_register(turbostat_registry, ARMCPUFreqReader)
    except ImportError as exc:
        logger.debug("bootstrap[turbostat]: ARMCPUFreqReader not importable: %s", exc)

    try:
        from core.readers.darwin.ioreport_cpufreq_reader import IOReportCPUFreqReader
        _safe_register(turbostat_registry, IOReportCPUFreqReader)
    except ImportError as exc:
        logger.debug("bootstrap[turbostat]: IOReportCPUFreqReader not importable: %s", exc)

    try:
        from core.readers.darwin.darwin_cpufreq_reader import DarwinCPUFreqReader
        _safe_register(turbostat_registry, DarwinCPUFreqReader)
    except ImportError as exc:
        logger.debug("bootstrap[turbostat]: DarwinCPUFreqReader not importable: %s", exc)


def register_msr_readers() -> None:
    """
    Register real MSR reader: MSRReader on Linux x86_64 only.

    Priority layout:
        MSRReader = 100  Linux x86_64
    """
    try:
        from core.readers.msr_reader import MSRReader
        _safe_register(msr_registry, MSRReader)
    except ImportError as exc:
        logger.debug("bootstrap[msr]: MSRReader not importable: %s", exc)


def register_scheduler_monitors() -> None:
    """
    Register real scheduler monitor: SchedulerMonitor on Linux and macOS.

    Priority layout:
        SchedulerMonitor = 100  Linux and macOS (graceful on Darwin via sysctl)
    """
    try:
        from core.readers.scheduler_monitor import SchedulerMonitor
        _safe_register(scheduler_registry, SchedulerMonitor)
    except ImportError as exc:
        logger.debug("bootstrap[scheduler]: SchedulerMonitor not importable: %s", exc)


def register_disk_readers() -> None:
    """
    Register real disk readers: DiskReader (Linux), IOKitDiskReader (macOS).

    Priority layout:
        DiskReader      = 100  Linux
        IOKitDiskReader = 100  macOS
        (mutually exclusive by OS)
    """
    try:
        from core.readers.disk_reader import DiskReader
        _safe_register(disk_registry, DiskReader)
    except ImportError as exc:
        logger.debug("bootstrap[disk]: DiskReader not importable: %s", exc)

    try:
        from core.readers.darwin.disk_reader import IOKitDiskReader
        _safe_register(disk_registry, IOKitDiskReader)
    except ImportError as exc:
        logger.debug("bootstrap[disk]: IOKitDiskReader not importable: %s", exc)

# ---------------------------------------------------------------------------
# Synthetic readers — registered alongside real readers.
# Selected ONLY when caps.platform_class == "synthetic" (SPEC 35C AC-6).
# On real hardware their can_handle() always returns False.
# ---------------------------------------------------------------------------
 
def register_synthetic_readers() -> None:
    """
    Register synthetic platform readers.
 
    These readers are eligible only when ALEMS_PLATFORM_OVERRIDE=synthetic
    is set. On real hardware (GN100, x86, macOS) can_handle() returns False
    and they are never selected. Registering them on all machines is safe
    and intentional — it means the synthetic platform works immediately on
    any machine without any install step.
    """
    try:
        from core.readers.synthetic_energy_reader import SyntheticEnergyReader
        _safe_register(energy_registry, SyntheticEnergyReader)
    except ImportError as exc:
        logger.warning("bootstrap[energy]: SyntheticEnergyReader not importable: %s", exc)
 
    try:
        from core.readers.synthetic_cpu_reader import SyntheticCPUReader
        _safe_register(cpu_registry, SyntheticCPUReader)
    except ImportError as exc:
        logger.warning("bootstrap[cpu]: SyntheticCPUReader not importable: %s", exc)
 
    try:
        from core.readers.synthetic_thermal_reader import SyntheticThermalReader
        _safe_register(thermal_registry, SyntheticThermalReader)
    except ImportError as exc:
        logger.warning("bootstrap[thermal]: SyntheticThermalReader not importable: %s", exc)

# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def register_external_reader_plugins() -> None:
    """
    Discover and register externally pip-installed readers via entry_points.

    Additive to the built-in registration above. Every reader family
    here has no per-plugin activation list, so any discovered external
    reader is an "optional" plugin — a broken external reader logs a
    warning and is skipped, never blocking startup.
    """
    family_groups = {
        "energy":    ("alems.readers.energy", energy_registry),
        "cpu":       ("alems.readers.cpu", cpu_registry),
        "thermal":   ("alems.readers.thermal", thermal_registry),
        "turbostat": ("alems.readers.turbostat", turbostat_registry),
        "msr":       ("alems.readers.msr", msr_registry),
        "scheduler": ("alems.readers.scheduler", scheduler_registry),
        "disk":      ("alems.readers.disk", disk_registry),
    }
    all_builtin = []
    all_external = []
    for family_name, (group, registry) in family_groups.items():
        builtin_before = list(registry.get_all().keys())
        names = discover_plugins(
            group=group,
            register_fn=lambda cls, r=registry: _safe_register(r, cls),
            core_version=_CORE_VERSION,
        )
        all_builtin.extend(builtin_before)
        all_external.extend(names)
        if names:
            logger.info(
                "bootstrap[%s]: %d external plugin(s) registered via entry_points",
                family_name, len(names),
            )
    print_adapter_summary("readers", all_builtin, all_external)
 
def register_all_readers() -> None:
    """
    Register every built-in real reader family.
 
    Called once from energy_engine.py before any ReaderFactory method.
    Matches SPEC 35 startup lifecycle Step 1 (discovery).
    Platform detection (Step 2) and reader selection (Step 3) happen after.
    """
    logger.info("bootstrap: registering all internal readers (SPEC 35A Phase 1)")
    register_energy_readers()
    register_cpu_readers()
    register_thermal_readers()
    register_turbostat_readers()
    register_msr_readers()
    register_scheduler_monitors()
    register_disk_readers()
    # SPEC 35C: synthetic readers registered on all machines.
    # Safe — can_handle() returns False on real hardware.
    register_synthetic_readers()
    # SPEC 35E: external plugins installed via pip, discovered through
    # entry_points. Runs after built-ins so INV-6 duplicate detection
    # catches a plugin that collides with a built-in METHOD_ID.
    register_external_reader_plugins()
    logger.info("bootstrap: registration complete")
