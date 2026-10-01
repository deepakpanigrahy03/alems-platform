#!/usr/bin/env python3
"""
================================================================================
REGISTRY SERVICE  —  core/registry/service.py
================================================================================

Purpose:
    Single facade over all existing family registries.
    Does not replace any existing registry object.
    Callers that already hold a direct registry reference are unaffected.

    API:
        RegistryService.discover(group)         list of registered names
        RegistryService.get(group, name)        class by name
        RegistryService.list(group)             dict of name -> class
        RegistryService.report()                full listing for alems plugins list

    Family -> group mappings mirror DESIGN_CHUNK39_v4.md section 3 table.
    Groups that have no registry yet (new harness groups for 39.4/39.5)
    return empty results without error — they are declared but not yet
    populated.

Author: Deepak Panigrahy
Spec:   SPEC_39_3, section 3
================================================================================
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Group -> (family label, registry accessor callable)
# Accessor is a lambda so imports stay lazy — never pulled in until needed.
# This avoids circular imports and preserves PAC-2: platform-specific
# readers are never imported at module load time of the service.
# ---------------------------------------------------------------------------

def _readers_registry():
    # type: () -> Dict[str, Any]
    """Return dict of all reader family registries keyed by group."""
    from core.readers.bootstrap import (
        energy_registry, cpu_registry, thermal_registry,
        turbostat_registry, msr_registry, scheduler_registry, disk_registry,
    )
    return {
        "alems.readers.energy":    energy_registry,
        "alems.readers.cpu":       cpu_registry,
        "alems.readers.thermal":   thermal_registry,
        "alems.readers.turbostat": turbostat_registry,
        "alems.readers.msr":       msr_registry,
        "alems.readers.scheduler": scheduler_registry,
        "alems.readers.disk":      disk_registry,
    }


def _platform_registry():
    # type: () -> Any
    from core.platform.bootstrap import platform_registry
    return platform_registry


def _text_registry():
    # type: () -> Any
    from core.execution.adapters.bootstrap import text_registry
    return text_registry


def _media_registry():
    # type: () -> Any
    from core.execution.adapters.bootstrap import media_registry
    return media_registry


def _scorer_registry():
    # type: () -> Any
    from core.execution.scorers.bootstrap import scorer_registry
    return scorer_registry


def _tool_registry():
    # type: () -> Any
    from core.execution.tools.bootstrap import tool_registry
    return tool_registry


def _selector_registry():
    # type: () -> Any
    from core.execution.tools.selector_bootstrap import selector_registry
    return selector_registry


def _framework_registry():
    # type: () -> Any
    from core.execution.frameworks.bootstrap import framework_registry
    return framework_registry


def _output_registry():
    # type: () -> Any
    from core.execution.outputs.bootstrap import output_registry
    return output_registry


def _serving_registry():
    # type: () -> Any
    from core.serving.registry import ServingEngineRegistry
    # ServingEngineRegistry is a class with class methods, not an instance.
    # Wrap it so get_all() works uniformly.
    class _ServingWrapper:
        def get_all(self):
            # type: () -> Dict[str, Any]
            return ServingEngineRegistry.get_all() if hasattr(ServingEngineRegistry, "get_all") else {}
        def is_empty(self):
            # type: () -> bool
            m = self.get_all()
            return len(m) == 0
    return _ServingWrapper()


# ---------------------------------------------------------------------------
# Master group table
# Each entry: group_name -> (family_label, accessor_callable_or_None)
# None accessor means the group is declared but registry not yet built
# (new harness groups, span vocabulary, attribution model — 39.4/39.5).
# ---------------------------------------------------------------------------

_GROUP_TABLE = {
    # measurement family
    "alems.platforms":             ("measurement", _platform_registry),
    "alems.readers.energy":        ("measurement", None),   # via _readers_registry()
    "alems.readers.cpu":           ("measurement", None),
    "alems.readers.thermal":       ("measurement", None),
    "alems.readers.turbostat":     ("measurement", None),
    "alems.readers.msr":           ("measurement", None),
    "alems.readers.scheduler":     ("measurement", None),
    "alems.readers.disk":          ("measurement", None),
    # execution family
    "alems.engines.text":          ("execution",   _text_registry),
    "alems.engines.media":         ("execution",   _media_registry),
    "alems.engines.serving":       ("execution",   _serving_registry),
    "alems.models.fragments":      ("execution",   None),   # not a class registry
    "alems.preflight.checks":      ("execution",   None),   # entry-points only
    "alems.harness.retry":         ("execution",   None),   # 39.4
    "alems.harness.recovery":      ("execution",   None),   # 39.4
    "alems.harness.collectors":    ("execution",   None),   # 39.4
    "alems.injection_engines":     ("execution",   None),   # no central registry yet
    "alems.scorers":               ("execution",   _scorer_registry),
    "alems.tools":                 ("execution",   _tool_registry),
    "alems.tool_selectors":        ("execution",   _selector_registry),
    "alems.frameworks":            ("execution",   _framework_registry),
    # semantic family
    "alems.spans.vocabularies":    ("semantic",    None),   # 39.4
    "alems.attribution.models":    ("semantic",    None),   # 39.4
    # persistence family
    "alems.databases":             ("persistence", None),   # DatabaseFactory, not adapter registry
    "alems.extensions":            ("persistence", None),   # ExtensionManager
    # output family
    "alems.outputs":               ("output",      _output_registry),
}

# Reader groups resolved via _readers_registry() not individual accessors.
_READER_GROUPS = {
    "alems.readers.energy", "alems.readers.cpu", "alems.readers.thermal",
    "alems.readers.turbostat", "alems.readers.msr", "alems.readers.scheduler",
    "alems.readers.disk",
}


class RegistryService:
    """
    Unified facade over all existing family registries.

    All methods are classmethods — no instantiation needed.
    Thread safety: same as the underlying registries (read-only after boot).
    """

    @classmethod
    def _resolve(cls, group):
        # type: (str) -> Optional[Any]
        """
        Return the registry object for a group, or None if not available.
        Reader groups go through _readers_registry() to get the right instance.
        """
        if group not in _GROUP_TABLE:
            logger.warning("RegistryService: unknown group '%s'", group)
            return None

        _family, accessor = _GROUP_TABLE[group]

        if group in _READER_GROUPS:
            # All reader registries from one call to avoid repeated imports.
            try:
                return _readers_registry().get(group)
            except Exception as exc:
                logger.warning("RegistryService: reader registry unavailable: %s", exc)
                return None

        if accessor is None:
            # Group declared but no registry built yet.
            return None

        try:
            return accessor()
        except Exception as exc:
            logger.warning(
                "RegistryService: could not access registry for '%s': %s", group, exc
            )
            return None

    @classmethod
    def list(cls, group):
        # type: (str) -> Dict[str, Any]
        """
        Return dict of name -> class for all registered entries in group.
        Empty dict if group has no registry or nothing is registered.
        """
        reg = cls._resolve(group)
        if reg is None:
            return {}
        try:
            return reg.get_all()
        except Exception as exc:
            logger.warning("RegistryService.list(%s): get_all() failed: %s", group, exc)
            return {}

    @classmethod
    def get(cls, group, name):
        # type: (str, str) -> Optional[Any]
        """
        Return the class registered under name in group.
        Returns None if not found rather than raising.
        """
        entries = cls.list(group)
        return entries.get(name)

    @classmethod
    def discover(cls, group):
        # type: (str) -> List[str]
        """
        Return list of registered names in group.
        """
        return list(cls.list(group).keys())

    @classmethod
    def report(cls):
        # type: () -> Dict[str, Any]
        """
        Full listing for alems plugins list.
        Returns dict: family -> group -> list of (name, class_name, origin).
        Origin is always 'builtin' at this layer — plugin_discovery tracks
        external vs builtin in its own logs; the service reports what is
        registered, not where it came from.
        """
        # Import lazily to avoid circular dependency at module load.
        from alems_sdk.conformance import run_conformance
        from alems_sdk.manifest import origin_of
        from importlib.metadata import packages_distributions

        # Origin by distribution, never by module name (WP 1a.3 4.1).
        pkg_dists = packages_distributions()
        result = {}
        for group, (family, _accessor) in _GROUP_TABLE.items():
            if family not in result:
                result[family] = {}
            entries = cls.list(group)
            rows = []
            for k, v in entries.items():
                meta = getattr(v, "ALEMS_PLUGIN_META", {})
                top = (getattr(v, "__module__", "") or "").split(".")[0]
                origin = origin_of((pkg_dists.get(top) or [None])[0])
                report = run_conformance(k, meta, cls=v, origin=origin)
                rows.append({
                    "name": k,
                    "class": v.__name__ if hasattr(v, "__name__") else str(v),
                    "conformance": "PASS" if report.passed else "FAIL",
                    "warnings": sum(len(r.warnings) for r in report.results),
                    "failures": [f for r in report.results for f in r.failures],
                })
            result[family][group] = rows
        return result
