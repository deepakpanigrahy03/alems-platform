# core/config/schema_bridge.py
# Converts the existing get_config_schema() dict format to JSON Schema
# draft 2020-12 so plugin manifests and the GDE can use a standard schema.
#
# SPEC_39_2A1_DELTA item B: no parallel mechanism; bridge only.
# Existing validation in plugin_config.py is unchanged.
# This module is additive: it adds JSON Schema output alongside the existing
# dict-based validation. It does not replace it.

from __future__ import annotations

from typing import Any, Dict, Optional

# Mapping from alems type strings to JSON Schema type strings.
_TYPE_MAP: Dict[str, str] = {
    "str":   "string",
    "int":   "integer",
    "float": "number",
    "bool":  "boolean",
}

# Optional x-alems-ui hint keys that pass through unchanged.
_UI_HINT_KEYS = frozenset(["label", "group", "order", "widget", "help"])


def alems_schema_to_jsonschema(
    alems_schema: Dict[str, Any],
    title: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Convert a get_config_schema() dict to JSON Schema draft 2020-12.

    Lossless for all fields the existing format supports:
      type, required, default.
    Optional x-alems-ui hints (label, group, order, widget, help) are
    preserved on each property if present in the source dict.

    Args:
        alems_schema: Dict returned by get_config_schema() on any ABC.
        title:        Optional schema title (plugin name or class name).

    Returns:
        A JSON Schema draft 2020-12 object schema dict.

    Example input:
        {
            "enabled": {"type": "bool", "required": False, "default": True},
            "mode":    {"type": "str",  "required": False, "default": None},
        }

    Example output:
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean", "default": True},
                "mode":    {"type": "string"},
            },
            "required": [],
            "additionalProperties": False,
        }
    """
    if not alems_schema:
        # Plugin declared no config — return an empty permissive schema.
        result: Dict[str, Any] = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }
        if title:
            result["title"] = title
        return result

    properties: Dict[str, Any] = {}
    required_keys = []

    for key, spec in alems_schema.items():
        alems_type = spec.get("type", "str")
        json_type = _TYPE_MAP.get(alems_type, "string")

        prop: Dict[str, Any] = {"type": json_type}

        default = spec.get("default")
        # Only include default when it is not None (None means no default).
        if default is not None:
            prop["default"] = default

        # Pass through optional x-alems-ui hints.
        for hint_key in _UI_HINT_KEYS:
            if hint_key in spec:
                prop[f"x-alems-ui_{hint_key}"] = spec[hint_key]

        properties[key] = prop

        if spec.get("required", False):
            required_keys.append(key)

    result = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "required": required_keys,
        "additionalProperties": False,
    }
    if title:
        result["title"] = title
    return result


def config_schema_for_plugin(cls: Any) -> Dict[str, Any]:
    """
    Return JSON Schema for a plugin class.

    If the class has get_config_schema(), converts it via
    alems_schema_to_jsonschema(). Otherwise returns an empty permissive
    schema. Never raises; logs a warning on get_config_schema() failure.

    Args:
        cls: Plugin class (any family).

    Returns:
        JSON Schema dict (draft 2020-12).
    """
    import logging
    logger = logging.getLogger(__name__)

    alems_schema: Dict[str, Any] = {}
    if callable(getattr(cls, "get_config_schema", None)):
        try:
            alems_schema = cls.get_config_schema()
        except Exception as exc:
            logger.warning(
                "schema_bridge: get_config_schema() on %s raised: %s",
                getattr(cls, "__name__", repr(cls)), exc,
            )

    title = getattr(cls, "__name__", None)
    return alems_schema_to_jsonschema(alems_schema, title=title)
