# tests/test_schema_bridge.py
# SPEC_39_2A1_DELTA item B.5: validation behavior of existing plugins unchanged.
# Tests cover: lossless conversion, required keys, defaults, empty schema,
# x-alems-ui hints, unknown type fallback, config_schema_for_plugin helper.

from __future__ import annotations

import pytest
from core.config.schema_bridge import alems_schema_to_jsonschema, config_schema_for_plugin


# ---------------------------------------------------------------------------
# alems_schema_to_jsonschema
# ---------------------------------------------------------------------------

def test_empty_schema_returns_permissive():
    result = alems_schema_to_jsonschema({})
    assert result["type"] == "object"
    assert result["properties"] == {}
    assert result["required"] == []
    assert result["additionalProperties"] is False
    assert result["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_type_mapping():
    schema = {
        "a": {"type": "str",   "required": False, "default": None},
        "b": {"type": "int",   "required": False, "default": None},
        "c": {"type": "float", "required": False, "default": None},
        "d": {"type": "bool",  "required": False, "default": None},
    }
    result = alems_schema_to_jsonschema(schema)
    assert result["properties"]["a"]["type"] == "string"
    assert result["properties"]["b"]["type"] == "integer"
    assert result["properties"]["c"]["type"] == "number"
    assert result["properties"]["d"]["type"] == "boolean"


def test_required_keys_collected():
    schema = {
        "name": {"type": "str", "required": True,  "default": None},
        "mode": {"type": "str", "required": False, "default": None},
    }
    result = alems_schema_to_jsonschema(schema)
    assert "name" in result["required"]
    assert "mode" not in result["required"]


def test_default_included_when_not_none():
    schema = {
        "enabled": {"type": "bool", "required": False, "default": True},
        "mode":    {"type": "str",  "required": False, "default": None},
    }
    result = alems_schema_to_jsonschema(schema)
    assert result["properties"]["enabled"]["default"] is True
    assert "default" not in result["properties"]["mode"]


def test_title_included_when_provided():
    result = alems_schema_to_jsonschema({}, title="MyPlugin")
    assert result["title"] == "MyPlugin"


def test_title_absent_when_not_provided():
    result = alems_schema_to_jsonschema({})
    assert "title" not in result


def test_ui_hints_passed_through():
    schema = {
        "level": {
            "type": "int",
            "required": False,
            "default": 1,
            "label": "Log level",
            "widget": "slider",
        }
    }
    result = alems_schema_to_jsonschema(schema)
    prop = result["properties"]["level"]
    assert prop["x-alems-ui_label"] == "Log level"
    assert prop["x-alems-ui_widget"] == "slider"


def test_unknown_type_falls_back_to_string():
    schema = {"x": {"type": "path", "required": False, "default": None}}
    result = alems_schema_to_jsonschema(schema)
    assert result["properties"]["x"]["type"] == "string"


def test_synthetic_reader_schema_converts():
    """Regression: synthetic_energy_reader.get_config_schema() converts cleanly."""
    from core.readers.synthetic_energy_reader import SyntheticEnergyReader
    alems_schema = SyntheticEnergyReader.get_config_schema()
    result = alems_schema_to_jsonschema(alems_schema, title="SyntheticEnergyReader")
    assert result["type"] == "object"
    assert "enabled" in result["properties"]
    assert result["properties"]["enabled"]["type"] == "boolean"
    assert result["properties"]["enabled"]["default"] is True
    assert result["title"] == "SyntheticEnergyReader"


# ---------------------------------------------------------------------------
# config_schema_for_plugin
# ---------------------------------------------------------------------------

def test_config_schema_for_plugin_with_schema():
    from core.readers.synthetic_energy_reader import SyntheticEnergyReader
    result = config_schema_for_plugin(SyntheticEnergyReader)
    assert result["type"] == "object"
    assert "enabled" in result["properties"]


def test_config_schema_for_plugin_without_method():
    class NoSchema:
        pass
    result = config_schema_for_plugin(NoSchema)
    assert result["type"] == "object"
    assert result["properties"] == {}


def test_config_schema_for_plugin_raises_gracefully():
    class BrokenSchema:
        @classmethod
        def get_config_schema(cls):
            raise RuntimeError("broken")
    result = config_schema_for_plugin(BrokenSchema)
    assert result["type"] == "object"
    assert result["properties"] == {}


# ---------------------------------------------------------------------------
# Existing plugin_config validation behavior unchanged (item B.5)
# ---------------------------------------------------------------------------

def test_plugin_config_still_validates_with_alems_schema():
    """
    plugin_config.load_plugin_config still uses the alems dict format.
    schema_bridge does not intercept or replace it.
    Verify load_plugin_config raises PluginConfigError for missing required key.
    """
    from core.config.plugin_config import load_plugin_config, PluginConfigError
    import tempfile, os, pathlib
    schema = {"api_key": {"type": "str", "required": True, "default": None}}
    # Pass a settings path with no plugins section.
    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        f.write("plugins: {}\n")
        tmp = f.name
    try:
        with pytest.raises(PluginConfigError):
            load_plugin_config("test_plugin", schema, settings_path=pathlib.Path(tmp))
    finally:
        os.unlink(tmp)
