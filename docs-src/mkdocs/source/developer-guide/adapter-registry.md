# A-LEMS Reader Adapter Registry

## Overview

Before this change, adding a new hardware reader to A-LEMS required editing
`ReaderFactory` in `core/readers/factory.py` — a 500-line file containing
platform-conditional `if/elif` chains for every reader family. Every new reader
risked breaking existing readers on platforms the developer could not test.
External contributors had no safe extension point at all.

The adapter registry solves this by inverting the dependency. Instead of
`ReaderFactory` knowing about every reader, each reader declares its own
eligibility. `ReaderFactory` asks the registry "who can handle this platform?"
and uses the answer. Adding a new reader is now one file plus one line in
`bootstrap.py` — no changes to `factory.py` required.

This document explains the design, the runtime lifecycle, the failure policy,
and how to extend the system. It covers the reader registry only. The full
adapter taxonomy covering serving engines, extensions, scorers, and output
formats is in SPEC 35 and ADAPTER_TAXONOMY_FULL.md.

## Why This Architecture

### The problem with if/elif dispatch

The old `ReaderFactory` worked like this:

```python
if caps.os == "Linux" and caps.is_grace_cpu and caps.has_spbm:
    return cls._make_spbm_reader(config, caps)
if caps.os == "Linux":
    return cls._make_rapl_reader(config)
if caps.os == "Darwin":
    return cls._make_iokit_reader(config)
return cls._make_dummy(config)
```

This means `factory.py` must be edited every time a new reader is added.
It also means the factory must import every reader — even readers for
platforms not present on the current machine. A developer adding an NVIDIA
DCGM reader for GPU energy measurement has to touch the same file that
handles RAPL and IOKit, risking regressions on machines they cannot test.

### The registry solution

Each reader now declares three things at class level:

```python
METHOD_ID: str = "rapl_msr_pkg_energy"   # unique identity
PRIORITY:  int = 100                      # dispatch order

@classmethod
def can_handle(cls, caps) -> bool:        # eligibility check
    return caps.os == "Linux" and caps.arch == "x86_64"
```

At startup, every real reader registers itself. When `ReaderFactory` needs
a reader, it asks the registry to select one based on the current
`PlatformCapabilities`. The factory no longer contains any platform logic
for the families covered by the registry — it calls `registry.select(caps)`
and uses whatever comes back.

### Why ABCs matter here

Every reader must inherit from its family ABC (`EnergyReaderABC`,
`ThermalReaderABC`, etc.). This is not just style. The ABC defines the
measurement contract — the methods core measurement code depends on.
When a reader inherits the ABC, Python enforces that every abstract method
is implemented at class definition time, not at the unpredictable moment
a measurement is attempted on a real machine during an experiment.

`DiskReaderABC` and `NICReaderABC` previously inherited from `ABC` directly,
bypassing `BaseReader`. This meant they did not get `can_handle()` and
`PRIORITY` from the shared base. Both were changed to inherit `BaseReader`
so the registry contract is defined exactly once and flows to every family
through a single inheritance chain.

## Runtime Lifecycle

The registry follows a strict startup ordering.

### Step 1: Registration (before platform detection)

`energy_engine.py` imports `bootstrap.py` at module load time and calls
`register_all_readers()` immediately. This happens before any
`ReaderFactory` method is invoked and before platform detection runs.

Each `register_<family>_readers()` function in `bootstrap.py` imports
reader classes in local `try/except` blocks. Platform-specific readers
that fail to import on the current machine (for example `IOKitPowerReader`
on Linux, or `SPBMEnergyReader` on macOS) are skipped with a debug log.
The platform never sees an import error from a reader it cannot use.

This preserves PAC-2 compliance: platform-conditional imports are isolated
and never leak into `energy_engine.py` or `harness.py`.

### Step 2: Platform detection

`get_platform_capabilities()` runs and produces a `PlatformCapabilities`
dataclass describing the current machine — OS, architecture, measurement
mode, and hardware flags like `has_spbm`, `has_arm_pmu`, `has_thermal`.

This happens after registration. The registry holds classes, not instances.
No reader is instantiated until selection is complete.

### Step 3: Selection

When `ReaderFactory.get_energy_reader()` is called, it queries the registry:

```python
winner_cls = energy_registry.select(caps)
```

`select()` calls `can_handle(caps)` on every registered class and keeps
those that return `True` (the eligible set). It then sorts by `PRIORITY`
ascending — lower number wins. If exactly one reader has the lowest
priority among eligible readers, it is returned. If two readers tie at
the same priority, `ConfigurationError` is raised immediately — ties are
never resolved silently. If no reader passes `can_handle()`,
`NoAdapterError` is raised and the factory falls back to a dummy reader
tagged as LIMITED.

### Priority assignments

Priority is only meaningful among readers whose `can_handle()` returned
True. On any real machine, only one primary reader is eligible per family
because eligibility conditions are mutually exclusive by OS and hardware:

| Reader | PRIORITY | Eligible when |
|---|---|---|
| RAPLReader | 100 | Linux x86_64 MEASURED |
| SPBMEnergyReader | 100 | Linux aarch64 Grace MEASURED |
| IOKitPowerReader | 100 | macOS MEASURED |
| EnergyEstimator | 500 | any INFERRED mode |
| IOReportCPUFreqReader | 100 | macOS (preferred freq reader) |
| DarwinCPUFreqReader | 200 | macOS (fallback freq reader) |

RAPL, SPBM, and IOKit are all PRIORITY=100 but can never tie because
no machine is simultaneously Linux x86_64, Linux aarch64 Grace, and macOS.
IOReport and DarwinCPUFreq are both macOS but at different priorities so
IOReport always wins when available.

## Where the Registry Lives

The registry is not a database table, a file, or a persistent object.
It is a set of module-level `AdapterRegistry` instances in `bootstrap.py`:

```python
energy_registry    = AdapterRegistry(family="energy")
cpu_registry       = AdapterRegistry(family="cpu")
thermal_registry   = AdapterRegistry(family="thermal")
...
```

These live in Python process memory. They are populated once when
`register_all_readers()` is called at process start and are gone when
the process exits. The next measurement run repopulates them from scratch
by re-importing `bootstrap.py`. There is no persistent registry state,
no migration, and no rollback procedure.

## Failure Policy

The old system silently returned `DummyEnergyReader` (zeros) whenever
platform detection produced an unexpected combination. A researcher could
run an entire experiment and not notice the energy values were all zero.

The new system makes failures explicit at three levels.

**No matching real reader.** `NoAdapterError` is raised by the registry.
`ReaderFactory` catches it, logs a WARNING, and returns the appropriate
dummy reader. The dummy is not in the registry — it is a factory-level
fallback only. Every LIMITED fallback is now visible in the logs.

**Tie at same priority.** `ConfigurationError` is raised immediately at
selection time, naming both tied readers. The process does not start.
This surfaces configuration mistakes at startup rather than during an
experiment.

**Duplicate METHOD_ID.** `DuplicateRegistrationError` is raised at
registration time, naming both conflicting classes. This catches two
readers claiming the same identity before any selection ever runs.

**Misbehaving can_handle().** If a reader's `can_handle()` raises an
exception, the registry logs a WARNING and treats that reader as
ineligible. Selection continues with the remaining readers. One broken
reader cannot prevent the platform from starting.

## Why Dummies Are Not in the Registry

An early design considered registering `DummyEnergyReader` at PRIORITY=999
with `can_handle() -> True` so it would always be selected as last resort.
This was rejected for two reasons.

First, it makes the dummy a competitor with real readers. When a plugin
author installs a new reader on a platform where a dummy previously won,
both are now eligible and priority arithmetic silently determines the
outcome. That is exactly the kind of hidden behavior the registry is
designed to eliminate.

Second, it misrepresents what a dummy is. A dummy does not answer "how do
I measure energy on this platform?" It answers "what should the software
do when energy measurement is unavailable?" Those are different
responsibilities. Keeping dummies out of the registry keeps that
distinction structurally enforced rather than conventional.

## Provenance and Database Impact

The registry has no database footprint. It does not write to any table,
does not add columns, and does not require any migration.

Reader-level measurement provenance is completely unchanged. Each reader
still declares `METHOD_ID`, `METHOD_CONFIDENCE`, and `METHOD_PROVENANCE`
as class attributes. These are still written to `measurement_method_registry`
and referenced from the `runs` table via `methodology_id` exactly as before.
The registry selects which reader wins. What that reader writes to the
database is entirely its own concern, governed by the same methodology
provenance rules as always.

## Platform Coverage

| Platform | Architecture | Family | Selected Reader | PRIORITY | Status |
|---|---|---|---|---|---|
| NVIDIA Grace GB10 | aarch64 | energy | SPBMEnergyReader | 100 | VERIFIED |
| NVIDIA Grace GB10 | aarch64 | cpu | ARMPMUReader | 100 | VERIFIED |
| NVIDIA Grace GB10 | aarch64 | thermal | ARMThermalReader | 100 | VERIFIED |
| NVIDIA Grace GB10 | aarch64 | turbostat | ARMCPUFreqReader | 100 | VERIFIED |
| Intel i7 Linux | x86_64 | energy | RAPLReader | 100 | VERIFIED |
| Intel i7 Linux | x86_64 | cpu | PerfReader | 100 | VERIFIED |
| Intel i7 Linux | x86_64 | thermal | SensorReader | 100 | VERIFIED |
| Intel i7 Linux | x86_64 | turbostat | TurbostatReader | 100 | VERIFIED |
| AMD Ryzen Linux | x86_64 | energy | RAPLReader | 100 | VERIFIED |
| AMD Ryzen Linux | x86_64 | cpu | PerfReader | 100 | VERIFIED |
| Apple Silicon macOS | arm64 | energy | IOKitPowerReader | 100 | PENDING |
| Apple Silicon macOS | arm64 | cpu | KPerfPMUReader | 100 | PENDING |
| Any platform | any | energy | NoAdapterError → DummyEnergyReader (LIMITED) | — | VERIFIED |

## Adding a New Internal Reader

A new internal reader requires exactly two changes and zero changes to
`factory.py`.

**Step 1.** Create the reader file. The class must inherit from the correct
family ABC and declare three class-level attributes:

```python
from core.readers.interfaces import EnergyReaderABC

class DCGMEnergyReader(EnergyReaderABC):
    """Reads GPU energy from NVIDIA DCGM API."""

    METHOD_ID: str = "dcgm_gpu_energy"
    PRIORITY:  int = 100

    @classmethod
    def can_handle(cls, caps) -> bool:
        """Eligible on Linux with DCGM available."""
        return caps.os == "Linux" and getattr(caps, "has_dcgm", False)

    # implement all EnergyReaderABC abstract methods
```

**Step 2.** Register it in `bootstrap.py`:

```python
def register_energy_readers() -> None:
    ...
    try:
        from core.readers.dcgm_energy_reader import DCGMEnergyReader
        _safe_register(energy_registry, DCGMEnergyReader)
    except ImportError as exc:
        logger.debug("bootstrap[energy]: DCGMEnergyReader not importable: %s", exc)
```

On machines without DCGM the import fails silently. On machines with DCGM
the reader registers and wins selection when `can_handle()` returns True.
No other file needs to change.

## Adding an External Plugin Reader

An external plugin registers by declaring in its `pyproject.toml`:

```toml
[project.entry-points."alems.readers.energy"]
dcgm = "alems_plugin_dcgm.reader:DCGMEnergyReader"
```

`pip install alems-reader-dcgm` installs the plugin. At next startup,
`importlib.metadata.entry_points()` discovers the class and registers it
automatically. No changes to any A-LEMS core file required.

See the plugin ecosystem catalog for the full list of planned and
community-contributed readers.

## Query Reference

The registry writes nothing to the database. To verify which reader ran
for a specific experiment:

```sql
-- Which reader was selected for a given run?
-- Applies to all platforms.
-- Expected on NVIDIA Grace GB10: method_id = 'spbm_pkg_v1'
-- Expected on x86 platforms:     method_id = 'rapl_msr_pkg_energy'
SELECT r.run_id,
       mmr.method_id,
       mmr.method_name,
       mmr.provenance,
       mmr.confidence
FROM runs r
JOIN measurement_method_registry mmr
  ON r.methodology_id = mmr.id
WHERE r.run_id = '<your-run-id>';
```

```sql
-- Most recent run reader selection on this machine.
SELECT r.run_id, r.created_at, mmr.method_id
FROM runs r
JOIN measurement_method_registry mmr ON r.methodology_id = mmr.id
ORDER BY r.created_at DESC
LIMIT 1;
```

## Verification

```bash
# Verify correct reader selected on this machine
python3 -c "
from core.readers.bootstrap import register_all_readers, energy_registry
register_all_readers()
from core.utils.platform import get_platform_capabilities
caps = get_platform_capabilities()
cls = energy_registry.select(caps)
print('Selected:', cls.__name__)
"

# Verify duplicate protection fires correctly
python3 -c "
from core.readers.bootstrap import register_all_readers, energy_registry
from core.readers.registry import DuplicateRegistrationError
from core.readers.rapl_reader import RAPLReader
register_all_readers()
try:
    energy_registry.register(RAPLReader)
    print('ERROR: should have raised')
except DuplicateRegistrationError:
    print('PASS: duplicate blocked')
"

# Run unit tests
python3 -m pytest tests/test_registry.py -v
```

## Known Limitations

**NIC readers not in registry.** `NICCollector` handles NIC reader
selection internally. The NIC family will be added to the registry in
a future release once the `NICReaderABC` interface is stable across all
platforms. Until then, NIC selection behavior is unchanged.

**DarwinCPUFreqReader constructor dependency.** `DarwinCPUFreqReader`
requires an `energy_reader` instance injected at construction time — a
dependency the registry cannot provide since it returns a class, not an
instance. `get_turbostat_reader()` in `factory.py` preserves the existing
candidate-list logic for this reader on macOS. This will be resolved in
a dedicated refactor of the Darwin turbostat path.
