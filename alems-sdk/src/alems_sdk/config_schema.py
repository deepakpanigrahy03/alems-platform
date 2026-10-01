"""
alems_sdk.config_schema: plugin configuration schema contract (design 7.14).

Every plugin declares its settings as JSON Schema (draft 2020-12): an object
schema whose properties are the keys read from plugins.<plugin_id> in the
configuration. A plugin with no settings declares NO_SETTINGS, which is what
every SDK contract inherits through Configurable. Unknown keys are rejected
at load (additionalProperties is false unless a schema says otherwise).
Validation itself is runtime work (core, jsonschema); the SDK only defines
the shape. Zero imports from core or scripts (INV-14).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# The explicit "this plugin reads no settings" schema.
NO_SETTINGS: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


class Configurable:
    """
    Base of every SDK plugin contract: declares the configuration schema.

    Override get_config_schema() (as a classmethod) when the plugin reads
    settings; the default declares that it reads none.
    """

    @classmethod
    def get_config_schema(cls) -> Dict[str, Any]:
        """JSON Schema of this plugin's settings; NO_SETTINGS by default."""
        return dict(NO_SETTINGS)


def schema_shape_error(schema: Any) -> Optional[str]:
    """
    Return why schema is not an acceptable plugin config schema, or None.

    Only the shape is checked here (object schema with a properties mapping);
    full validation of values is done by the runtime with jsonschema.
    """
    if not isinstance(schema, dict):
        return "config schema must be a dict, got %s" % type(schema).__name__
    if schema.get("type") != "object":
        return "config schema must be a JSON Schema object (type: object)"
    props = schema.get("properties", {})
    if not isinstance(props, dict):
        return "config schema properties must be a mapping"
    for key, spec in props.items():
        if not isinstance(spec, dict):
            return "config schema property '%s' must be a JSON Schema dict" % key
    return None


__all__ = ["NO_SETTINGS", "Configurable", "schema_shape_error"]
