#!/usr/bin/env python3
"""
scripts/tools/registry_snapshot.py: Rule S reference for plugin loading.

Runs every registration path the runtime uses today, then dumps every
registry as sorted lines:

    group | registry_key | module.qualname | entry_point_name | source_sha256

Order of discovery never matters (lines are sorted); object identity is
compared through the qualified name plus the SHA-256 of the defining source
file, which is what can be compared across processes.

Usage:
    PYTHONPATH=. venv/bin/python scripts/tools/registry_snapshot.py OUT.txt
"""

import hashlib
import importlib
import inspect
import sys
from importlib.metadata import entry_points


def _sha(cls) -> str:
    """SHA-256 (16 hex) of the file that defines cls; '-' if unavailable."""
    try:
        path = inspect.getsourcefile(cls)
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()[:16]
    except Exception:
        return "-"


def _ep_index():
    """Map (group, module:qualname) to entry point name for every alems group."""
    index = {}
    eps = entry_points()
    groups = eps.groups if hasattr(eps, "groups") else eps.keys()
    for group in groups:
        if not group.startswith("alems."):
            continue
        for ep in entry_points(group=group) if hasattr(eps, "select") else eps[group]:
            index[(group, ep.value.replace(":", "."))] = ep.name
    return index


def _run_bootstraps() -> None:
    """Call every bootstrap the runtime calls; failures are reported, not hidden."""
    calls = [
        ("core.readers.bootstrap", "register_all_readers"),
        ("core.platform.bootstrap", "register_all_platform_adapters"),
        ("core.execution.adapters.bootstrap", "register_all_adapters"),
        ("core.execution.frameworks.bootstrap", "register_all_frameworks"),
        ("core.execution.outputs.bootstrap", "register_all_outputs"),
        ("core.execution.scorers.bootstrap", "register_all_scorers"),
        ("core.execution.tools.bootstrap", "register_all_tool_providers"),
        ("core.execution.tools.selector_bootstrap", "register_all_selectors"),
    ]
    for mod, fn in calls:
        try:
            getattr(importlib.import_module(mod), fn)()
        except Exception as exc:
            print("BOOTSTRAP_ERROR %s.%s %s" % (mod, fn, exc), file=sys.stderr)
    try:
        from core.serving.registry import ServingEngineRegistry
        ServingEngineRegistry.discover()
    except Exception as exc:
        print("BOOTSTRAP_ERROR serving %s" % exc, file=sys.stderr)


def _mapping(obj):
    """Return {key: class} from a registry object of any of the current shapes."""
    for attr in ("get_all", "all", "list_all"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                result = fn()
                if isinstance(result, dict):
                    return result
            except TypeError:
                pass
    for attr in ("_classes", "_REGISTRY", "_registry", "_adapters", "_policies", "_collectors"):
        val = getattr(obj, attr, None)
        if isinstance(val, dict):
            return val
    return {}


def main(out_path: str) -> int:
    _run_bootstraps()
    index = _ep_index()
    lines = []

    from core.registry.service import RegistryService, _GROUP_TABLE
    for group in sorted(_GROUP_TABLE):
        try:
            entries = RegistryService.list(group) or {}
        except Exception as exc:
            lines.append("%s|ERROR|%s|-|-" % (group, exc))
            continue
        for key, cls in entries.items():
            fq = "%s.%s" % (getattr(cls, "__module__", "?"), getattr(cls, "__qualname__", "?"))
            lines.append("%s|%s|%s|%s|%s" % (group, key, fq, index.get((group, fq), "-"), _sha(cls)))

    # Registries outside the registry service (G43), dumped so their entry
    # into the single path is also checked.
    extra = [
        ("alems.engines.serving*", "core.serving.registry", "ServingEngineRegistry"),
        ("alems.harness.collectors*", "core.telemetry.cache_collector", "CacheTelemetryRegistry"),
        ("alems.harness.recovery*", "core.recovery.recovery_adapter", "RecoveryPolicyRegistry"),
    ]
    for label, mod, name in extra:
        try:
            reg = getattr(importlib.import_module(mod), name)
        except Exception as exc:
            lines.append("%s|ERROR|%s|-|-" % (label, exc))
            continue
        group = label.rstrip("*")
        mapping = _mapping(reg)
        if not mapping:
            # Some registries keep a module level _REGISTRY dict.
            mod_reg = getattr(importlib.import_module(mod), "_REGISTRY", None)
            mapping = mod_reg if isinstance(mod_reg, dict) else {}
        for key, cls in mapping.items():
            fq = "%s.%s" % (getattr(cls, "__module__", "?"), getattr(cls, "__qualname__", "?"))
            lines.append("%s|%s|%s|%s|%s" % (label, key, fq, index.get((group, fq), "-"), _sha(cls)))

    lines = sorted(set(lines))
    with open(out_path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print("%d registrations written to %s" % (len(lines), out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "registry_snapshot.txt"))
