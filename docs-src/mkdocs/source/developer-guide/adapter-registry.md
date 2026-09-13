---
**Method ID:** N/A (infrastructure — no new measurement method)
**Schema version:** N/A (no DB changes in SPEC 35A)
**Platforms verified:** NVIDIA Grace GB10 (aarch64), Intel/AMD Linux (x86_64), Apple Silicon macOS (arm64)
**Status:** PRODUCTION
**Last updated:** 2026-09-13
---

# A-LEMS Reader Adapter Registry

## Overview

The adapter registry replaces hardcoded `if/elif` dispatch in `ReaderFactory`
with a selection system where every reader class declares its own eligibility.
`ReaderFactory` tries the registry first on every `get_*` call.
If no real reader matches, `NoAdapterError` is raised, the factory logs a
WARNING, and returns a dummy explicitly tagged LIMITED — no silent zeros.

This document covers SPEC 35A (reader registry, Phase 1) only.
The full adapter taxonomy covering engines, extensions, and scorers is in
SPEC 35 and ADAPTER_TAXONOMY_FULL.md.

## Platform Coverage

| Platform | Architecture | Family | Selected Reader | PRIORITY | Status |
|---|---|---|---|---|---|
| NVIDIA Grace GB10 | aarch64 | energy | SPBMEnergyReader | 100 | VERIFIED |
| NVIDIA Grace GB10 | aarch64 | cpu | ARMPMUReader | 100 | VERIFIED |
| NVIDIA Grace GB10 | aarch64 | thermal | ARMThermalReader | 100 | VERIFIED |
| NVIDIA Grace GB10 | aarch64 | turbostat | ARMCPUFreqReader | 100 | VERIFIED |
| Intel/AMD Linux | x86_64 | energy | RAPLReader | 100 | VERIFIED |
| Intel/AMD Linux | x86_64 | cpu | PerfReader | 100 | VERIFIED |
| Intel/AMD Linux | x86_64 | thermal | SensorReader | 100 | VERIFIED |
| Intel/AMD Linux | x86_64 | turbostat | TurbostatReader | 100 | VERIFIED |
| Apple Silicon macOS | arm64 | energy | IOKitPowerReader | 100 | PENDING |
| Apple Silicon macOS | arm64 | cpu | KPerfPMUReader | 100 | PENDING |
| Apple Silicon macOS | arm64 | thermal | IOKitThermalReader | 100 | PENDING |
| Any | any | energy | NoAdapterError → DummyEnergyReader (LIMITED) | — | VERIFIED |

## Schema

No new database tables or columns in this phase.
The registry is an in-process Python dict, repopulated on every process start.

## Architectural Rules

**Dummies are not in the registry.**
`DummyEnergyReader`, `DummyThermalReader` etc. are factory-level fallbacks only.
When the registry raises `NoAdapterError`, factory logs WARNING and returns
the dummy with LIMITED status. No silent zeros anywhere.

**EnergyEstimator is in the registry.**
It is a real measurement attempt (INFERRED mode, model-estimated values),
not a zeros fallback. It registers at PRIORITY=500 and is selected when
`measurement_mode == "INFERRED"`.

**One contract, defined once.**
`PRIORITY` and `can_handle()` live on `BaseReader`.
All family ABCs inherit `BaseReader`, including `DiskReaderABC` and
`NICReaderABC` which previously bypassed it.

## Method Provenance

Reader-level methodology provenance is unchanged.
`METHOD_ID`, `METHOD_CONFIDENCE`, and `measurement_method_registry` continue
to be recorded exactly as before. The registry does not change what gets
written to the database.

## Adding a New Internal Reader

1. Create the reader class, subclassing the correct family ABC.
2. Add three declarations to the class:

```python
METHOD_ID: str = "your_unique_method_id"
PRIORITY: int  = 100

@classmethod
def can_handle(cls, caps) -> bool:
    return caps.os == "Linux" and caps.arch == "x86_64"
```

3. Add one block to `bootstrap.py` in the correct `register_*` function:

```python
try:
    from core.readers.your_reader import YourReader
    _safe_register(energy_registry, YourReader)
except ImportError as exc:
    logger.debug("bootstrap[energy]: YourReader not importable: %s", exc)
```

No changes to `factory.py` required.

## Query Reference

The registry writes nothing to the database. To verify which reader ran:

```sql
SELECT r.run_id,
       mmr.method_id,
       mmr.method_name,
       mmr.provenance
FROM runs r
JOIN measurement_method_registry mmr
  ON r.methodology_id = mmr.id
WHERE r.run_id = '<your-run-id>';
-- Replace <your-run-id> with actual run identifier.
-- Expected on GN100: method_id = 'spbm_pkg_v1'
-- Expected on x86:   method_id = 'rapl_msr_pkg_energy'
```

## Verification

```bash
# Verify registry selects correct reader on this machine
python3 -c "
from core.readers.bootstrap import register_all_readers, energy_registry
register_all_readers()
from core.utils.platform import get_platform_capabilities
caps = get_platform_capabilities()
cls = energy_registry.select(caps)
print('Selected:', cls.__name__)
"

# Verify DuplicateRegistrationError fires on double-registration
python3 -c "
from core.readers.bootstrap import register_all_readers, energy_registry
from core.readers.registry import DuplicateRegistrationError
from core.readers.rapl_reader import RAPLReader
register_all_readers()
try:
    energy_registry.register(RAPLReader)
    print('ERROR: should have raised')
except DuplicateRegistrationError:
    print('PASS: duplicate blocked correctly')
"

# Run unit tests
python3 -m pytest tests/test_registry.py -v

# Full regression run
python3 -m alems run --task gsm8k_linear 2>&1 | grep -E "registry selected|energy_uj"
```

## Known Limitations

- **NIC readers not in registry**: `NICCollector` handles NIC selection
  internally. NIC family will be added in a future chunk when the NIC
  reader selection logic is stable across all three platforms.

- **DarwinCPUFreqReader constructor dependency**: requires `energy_reader`
  injected at construction time. `get_turbostat_reader()` in factory.py
  preserves the existing candidate-list logic for this reader on Darwin.
  Will be resolved in a follow-up refactor.

- **Pre-existing PAC-2 violation in energy_engine.py line 77**: direct
  `from core.readers.rapl_reader import RAPLReader` import predates SPEC 35A.
  Out of scope for this chunk. Will be removed once that code path is
  confirmed unreachable after registry rollout.
