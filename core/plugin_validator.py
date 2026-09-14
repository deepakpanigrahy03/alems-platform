"""
================================================================================
PLUGIN VALIDATOR — ALEMS_PLUGIN_META Validation
================================================================================

PURPOSE:
    Validates the ALEMS_PLUGIN_META dict every external plugin must export.
    Called by plugin_discovery.py before a discovered entry point class is
    registered into any family registry.

    Checks:
      - required fields are present (name, version, alems_compat, description)
      - alems_compat is satisfied by the running core version
      - platform_constraint (if set) matches the current OS

AUTHOR: Deepak Panigrahy
================================================================================
"""

import logging
import sys
from typing import Any, Dict, List, Optional

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

logger = logging.getLogger(__name__)

# Fields every plugin must declare. Missing any of these is a hard failure —
# a plugin without a declared compatibility range cannot be safely loaded.
REQUIRED_META_FIELDS = ("name", "version", "alems_compat", "description")


class PluginValidationError(Exception):
    """Raised when a plugin's ALEMS_PLUGIN_META fails validation."""


def validate_meta(meta: Dict[str, Any], core_version: str) -> None:
    """
    Validate an ALEMS_PLUGIN_META dict against the running core version.

    Args:
        meta: The ALEMS_PLUGIN_META dict exported by the plugin package.
        core_version: alems.__version__ of the running core.

    Raises:
        PluginValidationError: on any validation failure. The caller
            decides whether this is fatal or a skip-with-warning — see
            the two failure classes in core/plugin_discovery.py.
    """
    if not isinstance(meta, dict):
        raise PluginValidationError(
            f"ALEMS_PLUGIN_META must be a dict, got {type(meta).__name__}"
        )

    missing = [f for f in REQUIRED_META_FIELDS if f not in meta]
    if missing:
        raise PluginValidationError(
            f"ALEMS_PLUGIN_META missing required fields: {missing}"
        )

    _check_compat(meta["alems_compat"], core_version, meta["name"])
    _check_platform_constraint(meta.get("platform_constraint"), meta["name"])


def _check_compat(compat_spec: str, core_version: str, plugin_name: str) -> None:
    """
    Check alems_compat against the running core version.

    Uses packaging.specifiers.SpecifierSet — the same mechanism pip uses
    for dependency resolution — rather than custom version comparison.
    """
    try:
        spec = SpecifierSet(compat_spec)
    except InvalidSpecifier as exc:
        raise PluginValidationError(
            f"Plugin '{plugin_name}': invalid alems_compat specifier "
            f"'{compat_spec}': {exc}"
        )

    try:
        version = Version(core_version)
    except InvalidVersion as exc:
        raise PluginValidationError(
            f"Plugin '{plugin_name}': cannot parse core version "
            f"'{core_version}': {exc}"
        )

    if version not in spec:
        raise PluginValidationError(
            f"Plugin '{plugin_name}' requires alems_compat '{compat_spec}' "
            f"but running core version is '{core_version}'"
        )


def _check_platform_constraint(constraint: Optional[Any], plugin_name: str) -> None:
    """
    Check platform_constraint against sys.platform.

    Coarse OS filter only (SPEC 35E section 3) — answers "can this
    plugin run on this OS at all", not "does the hardware match"
    (that is can_handle(caps), checked later at selection time).

    Matching uses sys.platform.startswith(constraint): "linux" matches
    sys.platform == "linux", "darwin" matches "darwin", "win32" matches
    "win32".
    """
    if constraint is None:
        return

    if isinstance(constraint, str):
        allowed: List[str] = [constraint]
    elif isinstance(constraint, list):
        allowed = constraint
    else:
        raise PluginValidationError(
            f"Plugin '{plugin_name}': platform_constraint must be None, "
            f"str, or List[str], got {type(constraint).__name__}"
        )

    if not any(sys.platform.startswith(p) for p in allowed):
        raise PluginValidationError(
            f"Plugin '{plugin_name}': platform_constraint {allowed} does "
            f"not match current platform '{sys.platform}'"
        )
