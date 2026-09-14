# A-LEMS Synthetic Platform

## What Problem This Solves

Every reader in A-LEMS requires specific hardware to function:

- `RAPLReader` requires Intel or AMD x86 Linux with RAPL sysfs at `/sys/class/powercap/`
- `SPBMEnergyReader` requires NVIDIA Grace GN100 with the spark_hwmon driver
- `IOKitPowerReader` requires Apple Silicon running macOS

This means the full A-LEMS measurement pipeline — registration, reader
selection, `EnergyCollector`, baseline subtraction, DB writes — cannot run
at all on a GitHub Actions runner, a developer laptop without the right
hardware, or a virtual machine. Every CI test that touches the measurement
pipeline either requires physical hardware or must mock at such a low level
that the test loses its value.

The synthetic platform solves this by providing a complete, real implementation
of the measurement pipeline that runs on any machine and returns deterministic,
configurable energy values from a YAML fixture file. The rest of the platform
— `EnergyCollector`, the harness, the database, baseline subtraction, all
analysis code — is completely unaware that the values are synthetic. It sees
a real `EnergyReaderABC` implementation returning real `NormalizedEnergyReading`
objects. The test can then assert exact values: `energy_uj == 1000000`, not
"non-zero and reasonable."

## How It Works

The synthetic platform is activated by a single environment variable:

```bash
ALEMS_PLATFORM_OVERRIDE=synthetic
```

When this variable is set, `get_platform_capabilities()` in
`core/utils/platform.py` intercepts before running `PlatformDetector.detect()`.
Instead of reading `hw_config.json` and `environment.json`, it returns a
`PlatformCapabilities` object with `platform_class="synthetic"` directly.

This `platform_class` field is the key. Every synthetic reader declares:

```python
@classmethod
def can_handle(cls, caps) -> bool:
    return getattr(caps, "platform_class", "") == "synthetic"
```

The registry calls `can_handle()` on every registered reader. On real hardware,
`platform_class` is `"nvidia_grace"`, `"intel_x86"`, `"amd_x86"`, or
`"apple_silicon"` — never `"synthetic"`. So synthetic readers are registered
on every machine but selected on no real machine. When
`ALEMS_PLATFORM_OVERRIDE=synthetic` is set, `platform_class` becomes
`"synthetic"` and the synthetic readers win selection.

The full startup sequence with synthetic override:

```
ALEMS_PLATFORM_OVERRIDE=synthetic set
        ↓
energy_engine.py imports bootstrap.py
        ↓
register_all_readers() called
        ↓
All readers registered (real + synthetic)
        ↓
get_platform_capabilities() called
        ↓
Override detected → PlatformCapabilities(platform_class="synthetic") returned
        ↓
ReaderFactory.get_energy_reader(caps) called
        ↓
energy_registry.select(caps) called
        ↓
can_handle() called on each reader:
    RAPLReader.can_handle()    → caps.os=="Linux" and caps.arch=="x86_64" → True*
    SPBMEnergyReader.can_handle() → caps.is_grace_cpu → False
    SyntheticEnergyReader.can_handle() → caps.platform_class=="synthetic" → True
        ↓
*Note: On Linux x86 machines, RAPL also passes can_handle but its
 measurement_mode check fails (synthetic caps have measurement_mode="MEASURED"
 but no actual RAPL paths). RAPL instantiation would fail gracefully.
 To guarantee synthetic wins, SyntheticEnergyReader should be at PRIORITY=50
 on the synthetic platform. Currently handled by the measurement_mode check
 in RAPLReader.can_handle() being satisfied but RAPL having no sysfs paths.
 A future improvement is to add a platform_class != "synthetic" guard to
 all real reader can_handle() implementations.
        ↓
SyntheticEnergyReader selected (PRIORITY=100)
        ↓
SyntheticEnergyReader(config) instantiated
        ↓
read_normalized() returns NormalizedEnergyReading(pkg_uj=1000000, ...)
        ↓
EnergyCollector, harness, DB — all proceed normally
        ↓
runs table: energy_uj == 1000000
```

## Three Measurement Modes

`SyntheticEnergyReader` supports three modes, configured via
`data/fixtures/synthetic_energy.yaml` or `config/app_settings.yaml`.

### Constant Mode (default)

Every call to `read_normalized()` returns the same values:

```yaml
mode: constant
constant:
  package_uj: 1000000
  core_uj:     600000
  dram_uj:     200000
```

Use this for CI assertions where you need to assert an exact value:

```python
assert run["energy_uj"] == 1000000
```

This is the default and the most useful mode for regression testing.

### Linear Mode

Values increase by a fixed increment per call:

```yaml
mode: linear
linear:
  start_package_uj: 1000000
  increment_uj:       50000
```

Call 1: `package_uj = 1000000`
Call 2: `package_uj = 1050000`
Call 3: `package_uj = 1100000`

Use this for testing delta computation, wraparound correction in
`EnergyCollector`, and baseline subtraction logic. With linear mode you
can verify that the harness correctly computes `workload_energy = stop - start`
across a known range.

### Replay Mode

Values are read from a CSV file, one row per call, wrapping at the end:

```yaml
mode: replay
replay:
  csv_path: data/fixtures/synthetic_energy_replay.csv
```

CSV format:
```
package_uj,core_uj,dram_uj
1000000,600000,200000
1050000,630000,210000
1100000,660000,220000
```

Use this for testing analysis code against realistic energy traces captured
from real hardware. You can capture a real measurement session's raw readings,
save them to CSV, and replay them deterministically in CI.

## What is and is Not Synthetic

Only the energy, CPU, and thermal readers are synthetic. Everything else
in the pipeline runs exactly as in production:

| Component | Synthetic? | Notes |
|---|---|---|
| `SyntheticEnergyReader` | Yes | Fixed fixture values |
| `SyntheticCPUReader` | Yes | Fixed PMU values |
| `SyntheticThermalReader` | Yes | Fixed 45°C |
| `EnergyCollector` | No | Real delta computation |
| `EnergyEngine` | No | Real measurement orchestration |
| Harness | No | Real task execution timing |
| Database writes | No | Real SQLite writes |
| Baseline subtraction | No | Real idle baseline |
| LLM inference | No | Real model inference |
| `ModelFactory` | No | Real adapter selection |

This means a synthetic platform run is a genuine integration test of
everything except the hardware counter reads. The database schema, the
measurement pipeline, the baseline logic, the harness timing — all of
these are exercised with real code paths.

## METHOD_CONFIDENCE = 0.0 and is_available() = True

These two facts about `SyntheticEnergyReader` may seem contradictory. They
are not.

`METHOD_CONFIDENCE` reflects physical measurement validity. It answers: "how
much do you trust that this reading reflects actual hardware energy
consumption?" For synthetic readings, the answer is zero — the values are
from a YAML file, not from hardware counters. Setting `METHOD_CONFIDENCE =
0.0` is scientifically correct and ensures that any analysis code filtering
on confidence correctly excludes synthetic runs from real-data analysis.

`is_available()` reflects operational status. It answers: "can this reader
function right now?" For `SyntheticEnergyReader`, the answer is always yes
— no hardware access is needed, so the reader is always available when the
synthetic platform is active.

Both are simultaneously correct. A reader can be available (operational)
and have zero confidence (not physically meaningful). In fact, this is
exactly the same relationship as `DummyEnergyReader`: it is available
(returns zeros without crashing), but its values have zero confidence (they
are zeros, not measurements).

## Structural Guarantee: Synthetic Never Selected on Real Hardware

This is a structural guarantee, not a convention.

On GN100: `caps.platform_class == "nvidia_grace"` — never `"synthetic"`.
On Intel/AMD: `caps.platform_class == "intel_x86"` or `"amd_x86"` — never `"synthetic"`.
On macOS: `caps.platform_class == "apple_silicon"` — never `"synthetic"`.

The only way `platform_class == "synthetic"` is if `ALEMS_PLATFORM_OVERRIDE=synthetic`
is explicitly set in the environment. This variable is never set in production,
never set in `~/.alemsrc` or `.alems-env` by the install scripts, and is
documented as a developer/CI-only tool.

A researcher running a real experiment on GN100 will never accidentally get
synthetic energy values in their database.

## Configuration Reference

Override the default fixture values by adding to `config/app_settings.yaml`:

```yaml
plugins:
  synthetic:
    mode: constant          # constant | linear | replay
    package_uj: 2000000     # 2 J for custom test
    core_uj: 1200000
    dram_uj: 400000
    increment_uj: 100000    # linear mode only
    csv_path: data/fixtures/my_trace.csv   # replay mode only
```

Config resolution order (first found wins):
1. `config/app_settings.yaml` `[plugins.synthetic]` section
2. `data/fixtures/synthetic_energy.yaml` defaults
3. Hardcoded defaults (package=1M, core=600K, dram=200K µJ)

## Query Reference

To identify synthetic runs in the database:

```sql
-- Find all synthetic platform runs.
-- Applies to: any platform DB.
-- Use this to exclude synthetic runs from paper analysis.
SELECT r.run_id,
       r.created_at,
       mmr.method_id,
       mmr.confidence,
       r.energy_uj
FROM runs r
JOIN measurement_method_registry mmr ON r.methodology_id = mmr.id
WHERE mmr.method_id = 'synthetic_energy'
ORDER BY r.created_at DESC;
```

```sql
-- Verify a synthetic run produced the expected energy value.
-- Expected: energy_uj = 1000000 (constant mode default).
SELECT run_id, energy_uj
FROM runs
WHERE run_id = '<your-run-id>';
-- Replace <your-run-id> with actual run identifier.
```

```sql
-- Confirm synthetic runs are excluded from real-data analysis queries.
-- Add this WHERE clause to any analysis query:
WHERE mmr.method_id != 'synthetic_energy'
-- Or equivalently:
WHERE mmr.confidence > 0.0
```

## Verification

```bash
# Verify synthetic platform activates correctly
ALEMS_PLATFORM_OVERRIDE=synthetic python3 -c "
from core.utils.platform import get_platform_capabilities
caps = get_platform_capabilities(force_refresh=True)
assert caps.platform_class == 'synthetic'
assert caps.measurement_mode == 'MEASURED'
print('PASS: synthetic caps produced correctly')
"

# Verify SyntheticEnergyReader selected by registry
ALEMS_PLATFORM_OVERRIDE=synthetic python3 -c "
from core.readers.bootstrap import register_all_readers, energy_registry
from core.utils.platform import get_platform_capabilities
register_all_readers()
caps = get_platform_capabilities(force_refresh=True)
cls = energy_registry.select(caps)
assert cls.__name__ == 'SyntheticEnergyReader'
print('PASS: registry selects SyntheticEnergyReader')
"

# Verify fixture values are correct
ALEMS_PLATFORM_OVERRIDE=synthetic python3 -c "
from core.readers.synthetic_energy_reader import SyntheticEnergyReader
r = SyntheticEnergyReader({})
reading = r.read_normalized()
assert reading.pkg_uj  == 1_000_000
assert reading.cpu_uj  ==   600_000
assert reading.dram_uj ==   200_000
print('PASS: fixture values correct')
"

# Verify synthetic NOT selected on real hardware
python3 -c "
from core.readers.bootstrap import register_all_readers, energy_registry
from core.utils.platform import get_platform_capabilities
register_all_readers()
caps = get_platform_capabilities(force_refresh=True)
cls = energy_registry.select(caps)
assert cls.__name__ != 'SyntheticEnergyReader'
print('PASS: synthetic not selected on real hardware:', cls.__name__)
"
```

## Known Limitations

**RAPLReader can_handle() on Linux x86 synthetic sessions.**
On a Linux x86 machine with `ALEMS_PLATFORM_OVERRIDE=synthetic`, real
readers such as `RAPLReader`, `PerfReader`, `SensorReader` are guarded
by `caps.platform_class != "synthetic"` in their `can_handle()` methods.
This means only `SyntheticEnergyReader` is eligible on all platforms when
the synthetic override is active. No tie can occur. The guard is applied
in the platform adapter system. See the platform adapter documentation for details.

**No synthetic turbostat, MSR, disk, scheduler, or NIC readers.**
Only energy, CPU, and thermal are synthetic in Part B. The other reader
families fall through to their dummy fallbacks on the synthetic platform.
This is sufficient for CI testing of the energy measurement pipeline. Full
synthetic coverage for all reader families is a future enhancement.

**Synthetic runs appear in the DB with real run_ids.**
There is no DB-level flag distinguishing synthetic from real runs today.
Use the `method_id = 'synthetic_energy'` query above to identify them.
A future enhancement could add a `run_type` column to the `runs` table.
