# tests/test_sdk_boundary_all.py
# 39.5.1a.2: the whole SDK is standalone (D2.1, INV-14) and every core
# compatibility path re-exports the identical SDK object (D2.2, Rule S).
# Run: venv/bin/python -m pytest tests/test_sdk_boundary_all.py -v
import importlib
import pathlib
import pkgutil
import subprocess
import sys

import pytest

import alems_sdk

# Every module of the SDK package, discovered, so a new module is covered
# automatically.
_SDK_MODULES = sorted(
    m.name for m in pkgutil.walk_packages(alems_sdk.__path__, "alems_sdk.")
)

_BLOCK_CORE = "import sys; sys.modules['core'] = None; sys.modules['scripts'] = None\n"

# (core compatibility path, SDK module) pairs moved in 39.5.1a.2.
_SHIMS = [
    ("core.readers.interfaces", "alems_sdk.readers"),
    ("core.readers.measurement_schema", "alems_sdk.measurement_schema"),
    ("core.models.normalized_energy_reading", "alems_sdk.energy_reading"),
    ("core.execution.scorers.abc", "alems_sdk.scoring"),
    ("core.execution.tools.abc", "alems_sdk.tools"),
    ("core.execution.tools.selector_abc", "alems_sdk.tool_selection"),
    ("core.execution.frameworks.abc", "alems_sdk.frameworks"),
    ("core.execution.outputs.abc", "alems_sdk.outputs"),
    ("core.database.base", "alems_sdk.storage"),
    ("core.extensions.abc", "alems_sdk.extensions"),
    ("core.injection.injection_engine", "alems_sdk.injection"),
]

# Contracts split out of mixed core modules: (core module, name, SDK module).
_SPLIT = [
    ("core.retry.retry_adapter", "RetryPolicyAdapter", "alems_sdk.policies"),
    ("core.recovery.recovery_adapter", "RecoveryPolicyAdapter", "alems_sdk.policies"),
    ("core.recovery.recovery_adapter", "RecoveryDecision", "alems_sdk.policies"),
    ("core.telemetry.cache_collector", "CacheTelemetryCollector", "alems_sdk.cache_telemetry"),
    ("core.telemetry.cache_collector", "StateReuseEvent", "alems_sdk.cache_telemetry"),
    ("core.telemetry.cache_collector", "CacheStateSnapshot", "alems_sdk.cache_telemetry"),
    ("core.execution.tools.real_tools", "ToolResult", "alems_sdk.tools"),
    ("core.platform.adapter", "PlatformAdapterABC", "alems_sdk.platforms"),
]


@pytest.mark.parametrize("module", _SDK_MODULES)
def test_sdk_module_imports_without_core(module):
    """Every SDK module imports with core and scripts unimportable."""
    code = _BLOCK_CORE + "import %s\n" % module
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]


def test_sdk_source_has_no_core_imports():
    """Static guard: no import of core or scripts anywhere in the SDK source."""
    root = pathlib.Path(alems_sdk.__file__).parent
    offenders = []
    for path in root.rglob("*.py"):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            s = line.strip()
            if s.startswith(("from core", "import core", "from scripts", "import scripts")):
                offenders.append("%s:%d" % (path.name, n))
    assert not offenders, offenders


@pytest.mark.parametrize("core_mod,sdk_mod", _SHIMS)
def test_shim_names_are_sdk_objects(core_mod, sdk_mod):
    """Every public SDK name is the identical object at the core path."""
    core = importlib.import_module(core_mod)
    sdk = importlib.import_module(sdk_mod)
    for name, obj in vars(sdk).items():
        if name.startswith("_") or not isinstance(obj, type):
            continue
        if obj.__module__ != sdk.__name__:
            continue  # imported helpers such as ABC
        assert getattr(core, name) is obj, "%s.%s" % (core_mod, name)


@pytest.mark.parametrize("core_mod,name,sdk_mod", _SPLIT)
def test_split_contracts_are_sdk_objects(core_mod, name, sdk_mod):
    """Contracts split from mixed modules are the SDK objects."""
    core = importlib.import_module(core_mod)
    sdk = importlib.import_module(sdk_mod)
    assert getattr(core, name) is getattr(sdk, name)


@pytest.mark.parametrize("facade", ["alems_sdk.measurement", "alems_sdk.harness",
                                    "alems_sdk.persistence"])
def test_facades_export_no_none(facade):
    """No conditional exports: every listed name is a real object (G16)."""
    mod = importlib.import_module(facade)
    missing = [n for n in mod.__all__ if getattr(mod, n, None) is None]
    assert not missing, missing


def test_core_platform_adapters_keep_runtime_helpers():
    """All core platform adapters inherit the runtime helpers (Rule S)."""
    from core.platform.adapter import PlatformRuntimeMixin
    from alems_sdk.platforms import PlatformAdapterABC
    import core.platform.adapters as pkg

    found = 0
    for m in pkgutil.iter_modules(pkg.__path__, pkg.__name__ + "."):
        mod = importlib.import_module(m.name)
        for obj in vars(mod).values():
            if isinstance(obj, type) and issubclass(obj, PlatformAdapterABC) \
                    and obj is not PlatformAdapterABC and obj.__module__ == mod.__name__:
                assert issubclass(obj, PlatformRuntimeMixin), obj.__name__
                found += 1
    assert found >= 8
