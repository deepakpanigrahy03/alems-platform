---
**Method ID:** plugin_conformance_v1
**Schema version:** no schema change (39.3b is code only)
**Platforms verified:** GN100 (aarch64), Lenovo UBUNTU2505 (x86-64)
**Status:** PRODUCTION
**Last updated:** 2026-09-26
---

## Overview { #plugin-conformance-overview }

A-LEMS loads measurement readers, execution engines, scorers, and other
components through a plugin system. Any plugin that violates its contract
(missing a required method, wrong fidelity declaration, missing error
bound on an estimator) would previously be discovered only at measurement
time, potentially corrupting a run silently.

The conformance kit system validates every plugin against its family
contract before it is used. Kits run at discovery time and their results
are visible in `alems plugins list`. Plugin authors run the same kits
during development.

## Platform Coverage { #plugin-conformance-platform-coverage }

| Platform | Architecture | Status | Notes |
|---|---|---|---|
| GN100 (gn100-2b96) | aarch64 | VERIFIED | 48 plugins, all PASS |
| Lenovo UBUNTU2505 | x86-64 | VERIFIED | gate F run |
| macOS arm64 | arm64 | PLANNED | gate F run |
| AMD Ryzen | x86-64 | PLANNED | gate F run |

## Schema { #plugin-conformance-schema }

No new tables or columns. Conformance results are computed at discovery
time and held in memory. They are not persisted to the DB because they
are a property of the installed plugin set, not of a run. The plugin
set is already recorded in the lock file (alems.lock) which is copied
into run provenance (INV-17).

## Method Provenance { #plugin-conformance-method-provenance }

Conformance checking is not a measurement method and has no method_id
or COLUMN_PROVENANCE entry. It is a contract enforcement mechanism.
The fidelity vocabulary it enforces (MEASURED, INFERRED, LIMITED,
CALCULATED) comes from PAC-3 (COMPLIANCE.md section 1).

## Kit Catalog { #plugin-conformance-kit-catalog }

Four kits ship in alems-sdk. Each covers one plugin family.

**Measurement kit** (`alems_sdk._kit_measurement.MeasurementKit`)
Covers: `alems.platforms`, `alems.readers.*` (all kinds).

Checks:
- `is_available()` present and callable without side effects.
- `get_name()` present.
- `FIDELITY` class attribute declared and within compliance vocabulary
  (MEASURED, INFERRED, LIMITED, CALCULATED).
- Readers with `FIDELITY=INFERRED` must declare `ERROR_BOUND`.
- `get_config_schema()` present (failure for first party, warning for
  external until SDK 1.0).
- No `core.*` imports (failure for external, warning for first party
  during strangler period per INV-14).
- `ALEMS_PLUGIN_META` manifest fields present.

**Execution kit** (`alems_sdk._kit_execution.ExecutionKit`)
Covers: engines, scorers, frameworks, preflight, harness components.

Checks:
- Manifest, config schema, no core imports (same as measurement).
- Serving engines: `capabilities()` method present.
- Preflight plugins: `run()` or `check()` method present.
- Scorers: `score_range` or `SCORE_RANGE` attribute present (warning
  if missing).

**Persistence kit** (`alems_sdk._kit_persistence.PersistenceKit`)
Covers: `alems.databases`, `alems.extensions`.

Checks:
- Manifest, config schema, no core imports.
- Extension plugins: `namespace` in meta or `NAMESPACE` class attribute
  (required, D5.2 DESIGN_CHUNK39_v4).

**Output kit** (`alems_sdk._kit_persistence.OutputKit`)
Covers: `alems.outputs`.

Checks:
- Manifest, config schema, no core imports.
- `export()` method present.

## Query Reference { #plugin-conformance-query-reference }

Conformance results are not in the DB. Query the installed state instead.

**List all plugins with conformance status**
```bash
alems plugins list
```
Expected output: one row per plugin showing name, class, and
`[PASS]` or `[FAIL Nw]` where N is the warning count.

**Run conformance programmatically against one plugin class**
```python
from alems_sdk.conformance import run_conformance
from core.readers.linux.rapl_reader import RAPLReader

meta = getattr(RAPLReader, "ALEMS_PLUGIN_META", {})
report = run_conformance("rapl_msr_pkg_energy", meta, cls=RAPLReader, origin="first_party")
print(report.passed)
for r in report.results:
    print(r.failures, r.warnings)
```

**Run conformance against all registered plugins**
```python
from core.registry.service import RegistryService
data = RegistryService.report()
for family, groups in data.items():
    for group, entries in groups.items():
        for e in entries:
            if e["conformance"] == "FAIL":
                print(family, group, e["name"], e["failures"])
```

**Check which first party plugins have strangler period warnings**
```python
from core.registry.service import RegistryService
data = RegistryService.report()
for family, groups in data.items():
    for group, entries in groups.items():
        for e in entries:
            if e["warnings"] > 0:
                print(e["name"], e["warnings"], "warnings")
```

## Verification { #plugin-conformance-verification }

```bash
cd ~/mydrive/alems-platform

# Reinstall SDK with kit modules
venv/bin/pip install -e alems-sdk

# Run conformance unit tests
venv/bin/python -m pytest tests/test_conformance.py -v

# Install fixture packages and run one-package test
venv/bin/pip install -e tests/fixtures/one_package/alems-fixture-reader
venv/bin/pip install -e tests/fixtures/one_package/alems-fixture-estimator
venv/bin/pip install -e tests/fixtures/one_package/alems-fixture-model
venv/bin/python -m pytest tests/test_one_package.py -v

# Confirm all registered plugins pass
scripts/alems plugins list
# Expected: every row shows [PASS] or [PASS Nw]; no [FAIL]
```

## Known Limitations { #plugin-conformance-known-limitations }

- **Strangler period core imports**: All 48 first party plugins show 3
  warnings each for `core.*` imports (INV-14). These are expected during
  the 39.1 to 39.5 strangler period. They become failures at Gate F.
  Tracked as B39-3a-1.

- **Config schema not enforced at load time**: `get_config_schema()` is
  checked for presence by the kit but the schema is not validated against
  actual plugin settings until 39.3 conformance kits are wired into the
  discovery path at load time. Currently kits run on demand (plugins list)
  and at one-package test time only.

- **No persistence**: Conformance results are not stored in the DB.
  A plugin that passes today and fails after an upgrade will show FAIL
  in the next `alems plugins list` but there is no historical record.
  The lock file (alems.lock) records the plugin version; cross-referencing
  lock versions gives the change history.

- **Column provenance not yet reader-owned**: The conformance kit checks
  fidelity declaration on the reader class but does not yet validate that
  the reader declares which columns it produces and what method applies
  to each. This is the subject of a forthcoming design (see
  PROBLEM_READER_PROVENANCE.md). Until that design is implemented,
  `COLUMN_PROVENANCE` in `core/utils/provenance.py` remains the single
  source of truth for column-to-method mapping and is platform-agnostic.
