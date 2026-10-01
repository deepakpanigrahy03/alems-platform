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

    The schema is JSON Schema draft 2020-12 (design 7.14): an object schema
    whose properties are the keys of plugins.<name>. Defaults declared in the
    schema are applied; unknown keys are rejected unless the schema sets
    additionalProperties; values are validated, never coerced.

    Args:
        name:          Plugin identity (entry point name).
        schema:        JSON Schema from get_config_schema(); empty means no settings.
        settings_path: Override for tests.

    Returns:
        Validated dict of config values, defaults applied.

    Raises:
        PluginConfigError: on any validation failure.
    """
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError

    settings = get_raw_settings(settings_path)
    plugins_section: Dict[str, Any] = settings.get("plugins") or {}
    raw: Dict[str, Any] = dict(plugins_section.get(name) or {})

    if not schema:
        # No settings declared: a configured section is an error, not silence.
        if raw:
            raise PluginConfigError(
                f"Plugin '{name}' declares no settings but plugins.{name} sets "
                f"{', '.join(sorted(raw))}"
            )
        return {}

    effective = dict(schema)
    # Unknown keys are rejected unless the schema decides otherwise (7.14).
    effective.setdefault("additionalProperties", False)
    try:
        Draft202012Validator.check_schema(effective)
    except SchemaError as exc:
        raise PluginConfigError(f"Plugin '{name}' has an invalid config schema: {exc.message}")

    result = _build_defaults(effective)
    result.update(raw)

    errors = sorted(
        Draft202012Validator(effective).iter_errors(result),
        key=lambda e: list(e.path),
    )
    if errors:
        detail = "; ".join(
            f"{'.'.join(str(p) for p in e.path) or '(root)'}: {e.message}" for e in errors
        )
        raise PluginConfigError(f"Plugin '{name}' configuration invalid under plugins.{name}: {detail}")
    return result


def _build_defaults(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Defaults declared by the JSON Schema properties (keys without a default are omitted)."""
    props = schema.get("properties") or {}
    return {key: spec["default"] for key, spec in props.items()
            if isinstance(spec, dict) and "default" in spec}
