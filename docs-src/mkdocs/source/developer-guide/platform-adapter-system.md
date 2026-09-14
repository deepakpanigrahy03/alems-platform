# A-LEMS Platform Adapter System

## Overview

Before this change, A-LEMS had no unified Python representation of a platform.
Hardware detection, provisioning, and verification were three separate concerns
handled by three separate scripts:

- `scripts/detect_hardware.py` — detects hardware, writes `hw_config.json`
- `scripts/platforms/<name>/provision.sh` — installs deps, sets permissions
- `scripts/verify_hardware.py` — checks that everything is accessible

These scripts worked, but a new platform required changes in all three places
plus `install.sh`. There was no Python object that said "I am a platform, here
is everything you need to know about me."

The platform adapter system solves this by unifying all three responsibilities
into a single `PlatformAdapterABC` subclass per platform. One file, one class,
three methods: `detect()`, `provision()`, `verify()`. `install.sh` and
`verify_hardware.py` become thin callers that go through the registry.

## Architecture

```
PlatformAdapterABC
    detect()    → hw_config dict or None
    provision() → ProvisionResult
    verify()    → VerificationResult

        ↓ implemented by

NVIDIAGraceAdapter    (PRIORITY=100, wraps NVIDIAGraceDetector)
AppleSiliconAdapter   (PRIORITY=100, wraps AppleSiliconDetector)
IntelLinuxAdapter     (PRIORITY=200, wraps IntelLinuxDetector)
AMDLinuxAdapter       (PRIORITY=200, wraps AMDLinuxDetector)
ARMLinuxAdapter       (PRIORITY=200, wraps GenericARMLinuxDetector)
RISCVLinuxAdapter     (PRIORITY=200, wraps RISCVLinuxDetector)
GenericLinuxAdapter   (PRIORITY=900, wraps GenericX86LinuxDetector)
SyntheticAdapter      (PRIORITY=950, active only on ALEMS_PLATFORM_OVERRIDE=synthetic)

        ↓ registered in

PlatformRegistry
    detect()    → calls detect() in PRIORITY order, first non-None wins
    provision() → delegates to active adapter
    verify()    → delegates to active adapter

        ↓ populated by

core/platform/bootstrap.py
    register_all_platform_adapters()
    called once from energy_engine.py at startup
```

## Detection Order

Adapters run in PRIORITY order (lower number runs first). The first adapter
whose `detect()` returns non-None wins and becomes the active adapter.

Same-priority adapters are allowed when they are mutually exclusive by
hardware. For example, `NVIDIAGraceAdapter` and `AppleSiliconAdapter` are
both PRIORITY=100 but can never both match on the same machine — Grace is
Linux aarch64 with SPBM, Apple Silicon is Darwin arm64. `IntelLinuxAdapter`
and `AMDLinuxAdapter` are both PRIORITY=200 but are mutually exclusive by
CPU vendor.

`SyntheticPlatformAdapter` at PRIORITY=950 is the absolute last resort. Its
`detect()` returns non-None only when `ALEMS_PLATFORM_OVERRIDE=synthetic` is
set. This means:

- On real hardware without the env var: synthetic never selected.
- With the env var: all real adapters run first and return None (wrong OS or
  arch), then synthetic matches at PRIORITY=950.

## Adding a New Platform

A new platform requires exactly one file and one line in `bootstrap.py`. No
changes to `install.sh`, `verify_hardware.py`, or any other file.

**Step 1.** Create `core/platform/adapters/my_platform.py`:

```python
from core.platform.adapter import PlatformAdapterABC, ProvisionResult, VerificationResult
from pathlib import Path
from typing import Any, Dict, Optional

_PROVISION_SH = str(Path(__file__).parent.parent.parent.parent /
                    "scripts" / "platforms" / "my_platform" / "provision.sh")

class MyPlatformAdapter(PlatformAdapterABC):

    PLATFORM_CLASS: str = "my_platform"
    PRIORITY:       int = 100   # or 200, 900 — see priority table

    def detect(self) -> Optional[Dict[str, Any]]:
        try:
            # Fast OS/arch pre-check before importing the detector
            import platform as _p
            if _p.system() != "Linux":
                return None
            from scripts.detect_hardware import MyDetector
            return MyDetector(verbose=False).detect()
        except Exception as exc:
            return None

    def provision(self) -> ProvisionResult:
        result = ProvisionResult(status="success")
        for sub in ("deps", "permissions"):
            step = self._run_provision_script(_PROVISION_SH, sub)
            result.add_step(step.name, step.status, step.message)
            if step.status == "failed":
                result.status = "partial"
        return result

    def verify(self) -> VerificationResult:
        import json
        hw_config = json.load(open("config/hw_config.json"))
        return self._run_verify_checks(hw_config)
```

**Step 2.** Add one line to `core/platform/bootstrap.py`:

```python
from core.platform.adapters.my_platform import MyPlatformAdapter
_safe_register(MyPlatformAdapter)
```

**Step 3.** Create `scripts/platforms/my_platform/provision.sh` and
`scripts/platforms/my_platform/verify.sh` if platform-specific scripts are
needed.

No other file needs to change. `install.sh` uses `${PLATFORM_DIR}/provision.sh`
which resolves from `platform_class`. `verify_hardware.py` calls `run_checks()`
which already dispatches on `platform_class`.

## ProvisionResult and VerificationResult

These dataclasses give structured, machine-readable outcomes from provision
and verify. They are designed for the GUI management layer (future) where each
check needs to render as a row with a pass/fail indicator.

**ProvisionResult:**
```python
result.status          # "success" | "partial" | "failed"
result.steps           # List[ProvisionStep]
result.requires_reboot # True if reboot needed before verify
```

**VerificationResult:**
```python
result.passed          # True if all critical checks passed
result.checks          # List[VerificationCheck]
```

Each `VerificationCheck` has:
```python
check.name        # "rapl" | "spbm" | "arm_pmu" etc.
check.description # Human-readable
check.passed      # bool
check.severity    # "critical" | "warning" | "info"
check.message     # Detail on failure
```

`severity="critical"` checks failing set `result.passed=False`.
`severity="warning"` checks failing do not — these are optional capabilities.
`severity="info"` checks are informational only.

This maps directly to what `verify_hardware.py`'s `optional` set already
encodes: `{"tsc", "turbostat", "ring_bus"}` become `severity="warning"`.

## Platform Coverage

| Platform | Adapter | PRIORITY | Detector |
|---|---|---|---|
| NVIDIA Grace GB10 | NVIDIAGraceAdapter | 100 | NVIDIAGraceDetector |
| Apple Silicon macOS | AppleSiliconAdapter | 100 | AppleSiliconDetector |
| Intel Linux x86_64 | IntelLinuxAdapter | 200 | IntelLinuxDetector |
| AMD Linux x86_64 | AMDLinuxAdapter | 200 | AMDLinuxDetector |
| Generic ARM Linux | ARMLinuxAdapter | 200 | GenericARMLinuxDetector |
| RISC-V Linux | RISCVLinuxAdapter | 200 | RISCVLinuxDetector |
| Unknown Linux x86 | GenericLinuxAdapter | 900 | GenericX86LinuxDetector |
| Synthetic (CI/test) | SyntheticPlatformAdapter | 950 | N/A (env var) |

## Relationship to detect_hardware.py

`detect_hardware.py` is not replaced. Every platform adapter's `detect()`
method calls the corresponding `PlatformDetector` subclass from
`detect_hardware.py` and returns its result. The adapter is a thin wrapper
that:

1. Does a fast OS/arch pre-check before importing the detector (so
   `NVIDIAGraceDetector` is never imported on macOS)
2. Catches all exceptions and returns None (adapter contract: never raise)
3. Exposes the detector's result as the `detect()` return value

`detect_hardware.py` continues to be callable as a standalone script
(`python scripts/detect_hardware.py`) and through `install.sh`. The adapter
system adds a Python-native path alongside the existing shell-script path.

## Verification

```bash
# Verify platform registry detects correctly
python3 -c "
from core.platform.bootstrap import register_all_platform_adapters, platform_registry
register_all_platform_adapters()
hw = platform_registry.detect()
print('platform_class:', hw.get('platform_class'))
print('active adapter:', platform_registry.active_platform_class)
"

# Verify synthetic adapter
ALEMS_PLATFORM_OVERRIDE=synthetic python3 -c "
from core.platform.bootstrap import register_all_platform_adapters, platform_registry
register_all_platform_adapters()
hw = platform_registry.detect()
assert hw['platform_class'] == 'synthetic'
result = platform_registry.verify()
assert result.passed
print('PASS: synthetic platform adapter works')
"

# Verify provision result structure
python3 -c "
from core.platform.bootstrap import register_all_platform_adapters, platform_registry
register_all_platform_adapters()
platform_registry.detect()
result = platform_registry.provision()
print('provision status:', result.status)
for step in result.steps:
    print(f'  {step.name}: {step.status}')
"
```

## Known Limitations

**`detect_hardware.py` still called directly by `install.sh` Pass 0.**
In install Pass 0, the venv does not exist yet. The platform adapter system
requires the venv (Python imports). So `install.sh` Pass 0 continues to call
`detect_hardware.py` as a subprocess to get `platform_class`. The adapter
system takes over after venv is active. This is correct and intentional —
the adapter is not a replacement for the fast shell-based detection in Pass 0.

**`verify_hardware.py` main() preserved.**
`verify_hardware.py`'s existing `main()` function is preserved unchanged.
The adapter-based `adapter_verify()` function is added alongside it. The
standalone `python scripts/verify_hardware.py` call in `install.sh` still
runs `main()` directly. The adapter path is for programmatic callers
(future GUI, CI scripts) that want structured `VerificationResult` objects.
