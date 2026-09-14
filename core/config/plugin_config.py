"""
core/config/plugin_config.py

Plugin and extension configuration loader for the A-LEMS adapter system.

Reads the plugins.<name> section from app_settings.yaml and validates it
against the schema declared by get_config_schema() on the adapter or
extension class.
Called once per plugin at startup, before the adapter constructor runs.
The validated dict is injected into the constructor so adapters never
read config files themselves.

Public API
----------
load_plugin_config(name, schema) -> dict
    Load, validate, and return the config dict for a named plugin.
    Raises PluginConfigError on any hard validation failure.
    Logs warnings for unknown keys.

get_raw_settings() -> dict
    Return the full parsed app_settings.yaml as a plain dict.
    Cached after first load so the file is read once per process.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------

class PluginConfigError(Exception):
    """
    Raised when a plugin's configuration in app_settings.yaml is invalid.

    This is a startup-time error — it surfaces before any experiment runs
    so the researcher knows exactly what to fix.
    """


# ---------------------------------------------------------------------------
# Settings cache
# ---------------------------------------------------------------------------

_settings_cache: Optional[Dict[str, Any]] = None
_settings_path: Optional[Path] = None


def _default_settings_path() -> Path:
    # core/config/plugin_config.py -> core/config/ -> core/ -> repo root -> config/
    return Path(__file__).parent.parent.parent / "config" / "app_settings.yaml"


def _load_settings(path: Path) -> Dict[str, Any]:
    """Parse app_settings.yaml and return a plain dict. Empty dict on any failure."""
    if not path.exists():
        logger.debug("plugin_config: app_settings.yaml not found at %s", path)
        return {}
    try:
        import yaml
        with open(path, "r") as fh:
            data = yaml.safe_load(fh) or {}
        logger.debug("plugin_config: loaded app_settings.yaml (%d top-level keys)", len(data))
        return data
    except Exception as exc:
        logger.warning("plugin_config: could not read app_settings.yaml: %s", exc)
        return {}


def get_raw_settings(settings_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Return the full parsed app_settings.yaml as a plain dict.

    Cached after first load — the file is read at most once per process.
    Pass settings_path only in tests to override the default location.

    Returns:
        Dict with the full settings content, or empty dict if file absent
        or unparseable.
    """
    global _settings_cache, _settings_path

    path = settings_path or _default_settings_path()

    # Return cache if same path was already loaded.
    if _settings_cache is not None and _settings_path == path:
        return _settings_cache

    _settings_path = path
    _settings_cache = _load_settings(path)
    return _settings_cache


def reset_cache() -> None:
    """
    Clear the settings cache.
    Intended for use in tests only — production code never calls this.
    """
    global _settings_cache, _settings_path
    _settings_cache = None
    _settings_path = None


# ---------------------------------------------------------------------------
# Type coercion
# ---------------------------------------------------------------------------

_TYPE_MAP = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
}


def _coerce(key: str, raw_value: Any, type_name: str) -> Any:
    """
    Coerce raw_value to the declared type.

    Raises PluginConfigError on type mismatch.
    YAML already parses integers and booleans correctly in most cases,
    but a user may write timeout_ms: "30000" (quoted) instead of 30000.
    We attempt a cast and raise on failure rather than silently ignoring.

    Args:
        key:        Config key name (for error messages).
        raw_value:  Value as parsed from YAML.
        type_name:  One of "str", "int", "float", "bool".

    Returns:
        Coerced value.

    Raises:
        PluginConfigError: If the value cannot be coerced to the declared type.
    """
    target = _TYPE_MAP.get(type_name)
    if target is None:
        # Unknown type in schema — treat as str, log warning.
        logger.warning(
            "plugin_config: unknown type '%s' for key '%s' — treating as str",
            type_name, key,
        )
        return str(raw_value)

    if isinstance(raw_value, target):
        return raw_value

    # Attempt coercion.
    try:
        return target(raw_value)
    except (ValueError, TypeError):
        raise PluginConfigError(
            f"Configuration key '{key}' must be of type {type_name}, "
            f"got {type(raw_value).__name__!r} with value {raw_value!r}."
        )


# ---------------------------------------------------------------------------
# Main loader
# ---------------------------------------------------------------------------

def load_plugin_config(
    name: str,
    schema: Dict[str, Any],
    settings_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Load and validate config for a named plugin from app_settings.yaml.

    Reads the plugins.<name> section.
    Validates every declared key against the schema from get_config_schema().
    Returns a typed dict ready for injection into the adapter constructor.

    Validation rules
    ----------------
    Missing required key (required=True, no default):
        Raises PluginConfigError naming the plugin and the key.
    Wrong type:
        Raises PluginConfigError naming the key and expected type.
    Unknown key (in YAML but not in schema):
        Logs a warning, ignores the key.
    Absent section, no required keys:
        Returns dict of schema defaults. No error, no warning.
    Absent section, required keys present:
        Raises PluginConfigError listing the required keys.

    Args:
        name:          Plugin identity string (ALEMS_PLUGIN_META["name"]).
        schema:        Dict from get_config_schema() on the adapter class.
        settings_path: Override for tests.

    Returns:
        Validated dict of config values for this plugin.

    Raises:
        PluginConfigError: On any hard validation failure.
    """
    if not schema:
        # Plugin declared no config — nothing to do.
        return {}

    settings = get_raw_settings(settings_path)
    plugins_section: Dict[str, Any] = settings.get("plugins") or {}
    raw: Dict[str, Any] = plugins_section.get(name) or {}

    # If the section is absent, check whether any required keys exist.
    if not raw:
        required_missing = [
            k for k, spec in schema.items()
            if spec.get("required", False) and spec.get("default") is None
        ]
        if required_missing:
            raise PluginConfigError(
                f"Plugin '{name}' requires configuration in app_settings.yaml "
                f"under plugins.{name} with keys: {', '.join(sorted(required_missing))}"
            )
        # No required keys — return defaults.
        return _build_defaults(schema)

    # Section is present. Warn on unknown keys.
    known = set(schema.keys())
    for k in raw:
        if k not in known:
            logger.warning(
                "plugin_config[%s]: unknown key '%s' in plugins.%s — ignored",
                name, k, name,
            )

    # Validate and coerce declared keys.
    result: Dict[str, Any] = {}
    for key, spec in schema.items():
        type_name = spec.get("type", "str")
        required = spec.get("required", False)
        default = spec.get("default")

        if key in raw:
            result[key] = _coerce(key, raw[key], type_name)
        elif required and default is None:
            raise PluginConfigError(
                f"Plugin '{name}': required configuration key '{key}' is missing "
                f"from plugins.{name} in app_settings.yaml."
            )
        else:
            # Use default (may be None if optional with no default).
            result[key] = default

    return result


def _build_defaults(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Return a dict of default values for all keys in schema."""
    return {key: spec.get("default") for key, spec in schema.items()}
