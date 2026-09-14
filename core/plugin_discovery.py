"""
================================================================================
PLUGIN DISCOVERY — entry_points() Loading for External Plugins
================================================================================

PURPOSE:
    Discovers externally pip-installed adapters via importlib.metadata
    entry_points() and registers them into the same family registries
    used by the built-in bootstrap.* modules.

    This is additive to bootstrap.py, not a replacement. Full bootstrap
    removal (SPEC 35E section 7) is a later, separate step that only
    happens after entry_point discovery is verified end to end. Built-in
    registration still runs first; this module discovers anything
    installed on top of it.

FAILURE SEMANTICS (SPEC 35E section 4):
    Unrequested/optional plugin failure:
        Discovered via entry_points but not explicitly named by the
        caller's active_names. Import, validation, or registration
        failure: log warning, skip, continue to the next entry point.
        Readers/engines/platforms have no per-plugin activation list,
        so every discovered plugin in those families is always
        "optional" — pass active_names=None (the default).

    Explicitly activated plugin failure:
        Name appears in active_names. Failure raises PluginLoadError.
        Only extensions have an activation list today (see
        core/extensions/manager.py, which implements this case itself
        rather than calling discover_plugins() directly).

AUTHOR: Deepak Panigrahy
================================================================================
"""

import logging
from importlib.metadata import entry_points
from typing import Callable, Iterable, List, Optional, Type

from core.plugin_validator import PluginValidationError, validate_meta
from core.config.plugin_config import load_plugin_config, PluginConfigError

logger = logging.getLogger(__name__)


class PluginLoadError(Exception):
    """Raised when an explicitly-activated plugin fails to load."""


def discover_plugins(
    group: str,
    register_fn: Callable[[Type, dict], None],
    core_version: str,
    active_names: Optional[Iterable[str]] = None,
) -> List[str]:
    """
    Discover and register all plugins for an entry_point group.
 
    After metadata validation, loads and validates plugin configuration
    from app_settings.yaml plugins.<name> against the schema declared
    by get_config_schema() on the adapter class.
    The validated config dict is passed to register_fn alongside the
    class so each bootstrap can inject it into the constructor.
 
    Args:
        group: Entry point group name, e.g. "alems.readers.energy".
        register_fn: Callable(cls, config) that registers a resolved
            class into the target family registry.
            config is a validated dict from load_plugin_config().
        core_version: alems.__version__ of the running core, passed to
            plugin_validator for alems_compat checks.
        active_names: If provided, plugin names in this collection are
            explicitly activated (failure raises PluginLoadError).
            Names not in it are optional (failure logs and skips). If
            None, every discovered plugin in this group is optional.
 
    Returns:
        List of plugin identity names successfully registered.
    """
    active = set(active_names) if active_names is not None else None
    registered = 0

    try:
        eps = entry_points(group=group)
    except Exception as exc:
        # entry_points() itself should not fail, but a corrupted package
        # metadata cache is a real-world failure mode worth catching.
        logger.warning(
            "plugin_discovery[%s]: entry_points() lookup failed: %s", group, exc
        )
        return 0

    registered_names = []

    for ep in eps:
        # Skip entry points owned by the alems-platform distribution
        # itself. Built-ins are declared in the root pyproject.toml so
        # the entry point groups are complete and documented, but they
        # are already registered by bootstrap.py's plain imports —
        # discovering them here too would mean validating a built-in
        # module against the external-plugin ALEMS_PLUGIN_META contract
        # it was never meant to satisfy.
        dist = getattr(ep, "dist", None)
        if dist is not None and _normalize(dist.name) == "alems-platform":
            continue

        is_explicit = active is not None and ep.name in active

        try:
            cls = ep.load()
        except Exception as exc:
            _handle_failure(group, ep.name, "load", exc, is_explicit)
            continue

        # ALEMS_PLUGIN_META lives on the plugin package's __init__.py,
        # not on the adapter class itself.
        meta = _load_plugin_meta(cls)
        if meta is None:
            _handle_failure(
                group, ep.name, "validate", "no ALEMS_PLUGIN_META found", is_explicit
            )
            continue

        try:
            validate_meta(meta, core_version)
        except PluginValidationError as exc:
            _handle_failure(group, ep.name, "validate", exc, is_explicit)
            continue
 
        # Load and validate plugin config from app_settings.yaml.
        # Uses the schema declared by get_config_schema() on the class.
        # Missing required keys raise PluginConfigError — treated as an
        # explicit activation failure (always fatal) or optional skip.
        schema = {}
        if callable(getattr(cls, "get_config_schema", None)):
            try:
                schema = cls.get_config_schema()
            except Exception as exc:
                logger.warning(
                    "plugin_discovery[%s]: get_config_schema() on %s raised: %s",
                    group, ep.name, exc,
                )
 
        try:
            plugin_cfg = load_plugin_config(ep.name, schema)
        except PluginConfigError as exc:
            _handle_failure(group, ep.name, "config", exc, is_explicit)
            continue
 
        try:
            register_fn(cls, plugin_cfg)
        except Exception as exc:
            _handle_failure(group, ep.name, "register", exc, is_explicit)
            continue

        logger.info(
            "plugin_discovery[%s]: registered external plugin '%s' (%s)",
            group, ep.name, cls.__name__,
        )
        registered += 1
        registered_names.append(ep.name)

    return registered_names


def _normalize(dist_name: str) -> str:
    """Normalize a distribution name for comparison (PEP 503 style)."""
    return dist_name.lower().replace("_", "-")

def _load_plugin_meta(cls: Type) -> Optional[dict]:
    """
    Read ALEMS_PLUGIN_META from the package containing cls.

    Returns None (rather than raising) if the package has no
    ALEMS_PLUGIN_META — this is treated as a validation failure by
    the caller, not a load failure.
    """
    try:
        package_name = cls.__module__.rsplit(".", 1)[0]
        package = __import__(package_name, fromlist=["ALEMS_PLUGIN_META"])
        return getattr(package, "ALEMS_PLUGIN_META", None)
    except Exception:
        return None


def _handle_failure(
    group: str, name: str, stage: str, reason: object, is_explicit: bool
) -> None:
    """
    Apply the two-class failure semantics from SPEC 35E section 4.

    Raises PluginLoadError for explicitly activated plugins so the
    caller can convert it into a fatal startup error. Logs a warning
    and returns for optional plugins so one broken plugin never blocks
    discovery of the rest.
    """
    if is_explicit:
        raise PluginLoadError(
            f"Plugin '{name}' in group '{group}' is explicitly activated "
            f"but failed at {stage}: {reason}"
        )
    logger.warning(
        "plugin_discovery[%s]: optional plugin '%s' failed at %s: %s — skipping",
        group, name, stage, reason,
    )
