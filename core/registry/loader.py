"""
core/registry/loader.py: the single path from an entry point to a registry.

    load -> manifest -> validate -> conformance -> config -> register

Every plugin, first party (runtime distribution) or external, enters every
registry only through load_group() (INV-2, D2.3, D3.6). The first failing
stage produces exactly one Refusal; the fatal versus optional decision is
taken once, here, never by a bootstrap: an explicitly activated plugin
raises PluginLoadError, an optional one is skipped with one warning and
listed as refused. Registries and their keys are never changed by the loader.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, entry_points, version
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from alems_sdk.conformance import run_conformance
from alems_sdk.manifest import PluginUnavailable, origin_of as _origin_of
from alems_sdk.manifest import (
    ORIGIN_RUNTIME,
    ManifestError,
    build_manifest,
    check_sdk_compat,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Refusal:
    """One plugin refused at one stage, with the reason."""

    group: str
    name: str
    stage: str
    reason: str


# Process wide record of the last load per (group, entry point name):
# origin for registered plugins, Refusal for refused ones. Read by
# alems plugins list and the startup banner.
ORIGINS: Dict[Tuple[str, str], str] = {}
REFUSALS: Dict[Tuple[str, str], Refusal] = {}


def _sdk_version() -> str:
    """Installed alems-sdk version: the single runtime truth for SDK range checks."""
    try:
        return version("alems-sdk")
    except PackageNotFoundError:
        return "0"


def _entry_points(group: str):
    """Entry points of group (Python 3.9 compatible)."""
    eps = entry_points()
    if hasattr(eps, "select"):
        return list(eps.select(group=group))
    return list(eps.get(group, []))


def _refuse(group: str, name: str, stage: str, reason: object, explicit: bool,
            level: int = logging.WARNING) -> None:
    """Record the refusal, then apply the fatal or optional policy once."""
    from core.plugin_discovery import PluginLoadError

    REFUSALS[(group, name)] = Refusal(group, name, stage, str(reason))
    ORIGINS.pop((group, name), None)
    if explicit:
        raise PluginLoadError(
            "Plugin '%s' in group '%s' is explicitly activated but was refused "
            "at %s: %s" % (name, group, stage, reason)
        )
    # Unavailable optional dependencies are expected; real faults are warnings.
    logger.log(level, "plugin[%s]: '%s' refused at %s: %s", group, name, stage, reason)


def load_group(
    group: str,
    register_fn: Callable[[type, dict], None],
    core_version: str,
    active_names: Optional[Iterable[str]] = None,
    origins: Optional[Iterable[str]] = None,
) -> List[str]:
    """
    Load every entry point of group through the single path.

    origins restricts loading to entry points whose distribution origin
    (runtime, external, local) is listed; None loads every origin.

    Args:
        group: entry point group.
        register_fn: callable(cls, config) that inserts into the family registry.
        core_version: runtime version for alems_compat checks.
        active_names: explicitly activated names (failure is fatal); others optional.

    Returns:
        Entry point names registered, in discovery order.
    """
    # Late imports: plugin_discovery delegates here, avoid an import cycle.
    from core.plugin_discovery import _load_plugin_meta, load_plugin_config
    from core.plugin_validator import _check_compat, _check_platform_constraint

    active = set(active_names or ())
    # None means every origin; a set filters before any import happens.
    allowed = set(origins) if origins is not None else None
    sdk_version = _sdk_version()
    registered: List[str] = []

    for ep in _entry_points(group):
        name = ep.name
        explicit = name in active
        # A reload starts clean: no stale refusal or origin survives (G64).
        REFUSALS.pop((group, name), None)
        ORIGINS.pop((group, name), None)

        # Origin filter before ep.load(): filtered plugins are never imported.
        dist = getattr(ep, "dist", None)
        if allowed is not None and _origin_of(getattr(dist, "name", None)) not in allowed:
            continue

        # Stage load.
        try:
            cls = ep.load()
        except ModuleNotFoundError as exc:
            # Missing third party dependency: expected on other platforms.
            # Missing module of the plugin's own package: a broken install.
            own_root = ep.value.split(":")[0].split(".")[0]
            missing_root = (exc.name or "").split(".")[0]
            if missing_root and missing_root != own_root:
                _refuse(group, name, "unavailable", exc, explicit, level=logging.INFO)
            else:
                _refuse(group, name, "load", exc, explicit)
            continue
        except Exception as exc:
            _refuse(group, name, "load", exc, explicit)
            continue

        # Stage manifest: declared META merged with derived facts.
        try:
            manifest = build_manifest(
                entry_point_name=name,
                group=group,
                cls=cls,
                declared=_load_plugin_meta(cls),
                dist_name=getattr(dist, "name", None),
                dist_version=getattr(dist, "version", None),
                dist_requires=getattr(dist, "requires", None),
                sdk_version=sdk_version,
            )
        except Exception as exc:
            _refuse(group, name, "manifest", "%s: %s" % (type(exc).__name__, exc), explicit)
            continue

        # Data plugins (model fragments) are checked through their module.
        manifest.extra.setdefault("source_module", getattr(ep, "module", None))

        # Stage validate: SDK range, runtime compatibility, platform.
        try:
            check_sdk_compat(manifest, sdk_version)
            if manifest.alems_compat:
                _check_compat(manifest.alems_compat, core_version, name)
            if manifest.platforms:
                _check_platform_constraint(list(manifest.platforms), name)
        except Exception as exc:
            _refuse(group, name, "validate", exc, explicit)
            continue

        # Stage conformance: gating, not reporting (G15).
        try:
            report = run_conformance(name, manifest.as_dict(), cls=cls, origin=manifest.origin)
        except Exception as exc:
            _refuse(group, name, "conformance", "%s: %s" % (type(exc).__name__, exc), explicit)
            continue
        if not report.passed:
            failures = "; ".join(f for r in report.results for f in r.failures)
            _refuse(group, name, "conformance", failures, explicit)
            continue

        # Stage config: validated against the plugin's declared schema.
        # Callables (preflight, model fragments) declare no settings schema.
        try:
            getter = getattr(cls, "get_config_schema", None) if isinstance(cls, type) else None
            schema = getter() if callable(getter) else {}
            config = load_plugin_config(name, schema or {})
        except Exception as exc:
            # Any failure here is a refusal at stage config, never a crash.
            _refuse(group, name, "config", "%s: %s" % (type(exc).__name__, exc), explicit)
            continue

        # Stage register: the family registry keeps its own keys.
        try:
            register_fn(cls, config)
        except PluginUnavailable as exc:
            # Family availability rule said no: visible, not a fault.
            _refuse(group, name, "unavailable", exc, explicit, level=logging.INFO)
            continue
        except Exception as exc:
            _refuse(group, name, "register", exc, explicit)
            continue

        ORIGINS[(group, name)] = manifest.origin
        registered.append(name)

    return registered


def names_by_origin(group: str, runtime: bool) -> List[str]:
    """Registered entry point names of group from the runtime (or not)."""
    return [n for (g, n), o in ORIGINS.items()
            if g == group and (o == ORIGIN_RUNTIME) == runtime]


def preview_group(group: str, core_version: str) -> Tuple[List[str], List[Refusal]]:
    """Run every stage except register; for audits and the gate (no side effects)."""
    seen: List[str] = []
    names = load_group(group, lambda cls, cfg: seen.append(cls), core_version)
    refused = [r for (g, _), r in REFUSALS.items() if g == group]
    return names, refused


__all__ = ["Refusal", "ORIGINS", "REFUSALS", "load_group", "names_by_origin", "preview_group"]
