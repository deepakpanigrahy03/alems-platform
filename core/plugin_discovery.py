"""
================================================================================
PLUGIN DISCOVERY HELPERS  —  core/plugin_discovery.py
================================================================================

PURPOSE:
    Shared helpers for the single plugin path (core.registry.loader.load_group):
    PluginLoadError, raised when an explicitly activated plugin is refused,
    and _load_plugin_meta, which reads ALEMS_PLUGIN_META from a plugin package.

    Discovery and registration live only in core.registry.loader
    (WP 39.5.1a.3, INV-2, D3.6). Built-in and external plugins take the same
    path; stage order and refusal semantics are defined there.

AUTHOR: Deepak Panigrahy
================================================================================
"""

import logging
from typing import Optional, Type

logger = logging.getLogger(__name__)


class PluginLoadError(Exception):
    """Raised when an explicitly-activated plugin fails to load."""



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


