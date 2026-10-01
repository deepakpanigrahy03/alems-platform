"""
alems_sdk.manifest: the single plugin manifest contract (D3.2, G41).

One schema for every plugin, first party or external. The runtime loader and
the conformance kits both use it; no other manifest rule exists.

Derivation rules (binding, WP 39.5.1a.3 design section 3.1):
    extension_point  the entry point group is authoritative
    family           only from GROUP_FAMILY (one family per group by construction)
    version          from the distribution that declares the entry point;
                     declared value required when there is no distribution
    plugin_id        declared value or the entry point name
    description      declared value or the first docstring line
A declared value that disagrees with a derived one is a ManifestError; the
declaration is fixed, the rule is never loosened.
Zero imports from core or scripts (INV-14).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, Optional, Tuple

# Every plugin group and its family (D3.2a). A dict keyed by group, so one
# group can never map to two families.
GROUP_FAMILY: Dict[str, str] = {
    # measurement
    "alems.platforms": "measurement",
    "alems.readers.energy": "measurement",
    "alems.readers.cpu": "measurement",
    "alems.readers.thermal": "measurement",
    "alems.readers.turbostat": "measurement",
    "alems.readers.msr": "measurement",
    "alems.readers.scheduler": "measurement",
    "alems.readers.disk": "measurement",
    # execution
    "alems.engines.text": "execution",
    "alems.engines.media": "execution",
    "alems.engines.serving": "execution",
    "alems.models.fragments": "execution",
    "alems.preflight.checks": "execution",
    "alems.harness.retry": "execution",
    "alems.harness.recovery": "execution",
    "alems.harness.collectors": "execution",
    "alems.injection_engines": "execution",
    "alems.scorers": "execution",
    "alems.tools": "execution",
    "alems.tool_selectors": "execution",
    "alems.frameworks": "execution",
    # semantic
    "alems.spans.vocabularies": "semantic",
    "alems.attribution.models": "semantic",
    # persistence
    "alems.databases": "persistence",
    "alems.extensions": "persistence",
    # output
    "alems.outputs": "output",
}

# Distribution that is the runtime itself (D2.1): its components may import core.
RUNTIME_DISTRIBUTION = "alems-platform"

ORIGIN_RUNTIME = "runtime"     # declared by the runtime distribution
ORIGIN_EXTERNAL = "external"   # any other installed distribution
ORIGIN_LOCAL = "local"         # no distribution (sandbox local plugin, D3.1)


class PluginUnavailable(Exception):
    """
    Raised by a family register function when its availability rule says no
    (optional dependency missing). Recorded as a refusal at stage unavailable,
    logged at info; still fatal for an explicitly activated plugin.
    """


class ManifestError(ValueError):
    """The manifest cannot be built or contradicts its entry point."""


@dataclass(frozen=True)
class PluginManifest:
    """Validated manifest of one plugin (D3.2)."""

    plugin_id: str
    extension_point: str
    family: str
    version: str
    sdk_range: str
    description: str
    origin: str
    distribution: Optional[str] = None
    platforms: Optional[Tuple[str, ...]] = None
    required_privileges: Tuple[str, ...] = ()
    capabilities: Tuple[str, ...] = ()
    alems_compat: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        """Plain dict form, as passed to conformance kits."""
        return asdict(self)


def _norm(name: Optional[str]) -> str:
    """Normalize a distribution name (PEP 503)."""
    return (name or "").lower().replace("_", "-").replace(".", "-")


def origin_of(dist_name: Optional[str]) -> str:
    """Origin from the declaring distribution; never from module names."""
    if not dist_name:
        return ORIGIN_LOCAL
    if _norm(dist_name) == RUNTIME_DISTRIBUTION:
        return ORIGIN_RUNTIME
    return ORIGIN_EXTERNAL


def _sdk_range_from_requires(requires: Optional[Iterable[str]]) -> Optional[str]:
    """Specifier of the alems-sdk requirement of a distribution, if any."""
    if not requires:
        return None
    try:
        from packaging.requirements import Requirement
    except ImportError:  # packaging is an SDK dependency; defensive only
        return None
    for line in requires:
        try:
            req = Requirement(line)
        except Exception:
            continue
        if _norm(req.name) == "alems-sdk" and not req.marker:
            return str(req.specifier) or None
    return None


def _major_range(version: str) -> str:
    """'>=M.0,<M+1.0' for the major of version."""
    major = int(version.split(".")[0])
    return ">=%d.0,<%d.0" % (major, major + 1)


def _first_doc_line(cls: Any) -> str:
    """First non empty docstring line of cls, or ''."""
    for line in (getattr(cls, "__doc__", "") or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _agree(field_name: str, declared: Any, derived: Any) -> None:
    """Raise when a declared value contradicts the derived one."""
    if declared is not None and derived is not None and str(declared) != str(derived):
        raise ManifestError(
            "declared %s '%s' contradicts derived '%s'" % (field_name, declared, derived)
        )


def build_manifest(
    entry_point_name: str,
    group: str,
    cls: Any,
    declared: Optional[Dict[str, Any]] = None,
    dist_name: Optional[str] = None,
    dist_version: Optional[str] = None,
    dist_requires: Optional[Iterable[str]] = None,
    sdk_version: Optional[str] = None,
) -> PluginManifest:
    """
    Build and validate the manifest of one entry point.

    Args:
        entry_point_name: entry point name.
        group: entry point group (authoritative extension point).
        cls: loaded plugin object.
        declared: ALEMS_PLUGIN_META of the plugin package, or None.
        dist_name, dist_version, dist_requires: declaring distribution facts.
        sdk_version: installed alems-sdk version (for the runtime sdk_range).

    Returns:
        PluginManifest.

    Raises:
        ManifestError on an unknown group, a missing required value, or any
        declared value contradicting a derived one.
    """
    d = dict(declared or {})

    if group not in GROUP_FAMILY:
        raise ManifestError("unknown extension point group '%s'" % group)
    family = GROUP_FAMILY[group]
    _agree("extension_point", d.get("extension_point"), group)
    _agree("family", d.get("family"), family)

    # Legacy key name (35E manifests) means plugin_id.
    declared_id = d.get("plugin_id", d.get("name"))
    _agree("plugin_id", declared_id, entry_point_name)

    origin = origin_of(dist_name)
    if dist_version:
        _agree("version", d.get("version"), dist_version)
        version = dist_version
    elif d.get("version"):
        version = str(d["version"])
    else:
        raise ManifestError("no version: no distribution and none declared")

    sdk_range = d.get("sdk_range") or _sdk_range_from_requires(dist_requires)
    if not sdk_range and origin == ORIGIN_RUNTIME and sdk_version:
        sdk_range = _major_range(sdk_version)
    if not sdk_range:
        raise ManifestError("no sdk_range: not declared and no alems-sdk requirement")

    description = d.get("description") or _first_doc_line(cls)
    if not description:
        raise ManifestError("no description: not declared and no docstring")

    platforms = d.get("platforms", d.get("platform_constraint"))
    if isinstance(platforms, str):
        platforms = (platforms,)

    known = {"plugin_id", "name", "extension_point", "family", "version",
             "sdk_range", "description", "platforms", "platform_constraint",
             "required_privileges", "capabilities", "alems_compat"}
    return PluginManifest(
        plugin_id=entry_point_name,
        extension_point=group,
        family=family,
        version=version,
        sdk_range=str(sdk_range),
        description=description,
        origin=origin,
        distribution=dist_name,
        platforms=tuple(platforms) if platforms else None,
        required_privileges=tuple(d.get("required_privileges") or ()),
        capabilities=tuple(d.get("capabilities") or ()),
        alems_compat=d.get("alems_compat"),
        extra={k: v for k, v in d.items() if k not in known},
    )


def check_sdk_compat(manifest: PluginManifest, sdk_version: str) -> None:
    """Raise ManifestError when the installed SDK is outside sdk_range (D4.2)."""
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version

    if Version(sdk_version) not in SpecifierSet(manifest.sdk_range):
        raise ManifestError(
            "requires alems-sdk %s; installed %s" % (manifest.sdk_range, sdk_version)
        )


__all__ = [
    "GROUP_FAMILY", "RUNTIME_DISTRIBUTION", "ORIGIN_RUNTIME", "ORIGIN_EXTERNAL",
    "ORIGIN_LOCAL", "ManifestError", "PluginManifest", "PluginUnavailable", "origin_of",
    "build_manifest", "check_sdk_compat",
]
