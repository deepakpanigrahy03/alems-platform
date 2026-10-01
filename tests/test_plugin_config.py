"""
tests/test_plugin_config.py

Unit tests for core/config/plugin_config.load_plugin_config under the single
JSON Schema contract (design 7.14, 39.5.1a.3, G47). Replaces the 35F tests,
which asserted the retired flat format (coercion, unknown keys ignored).

Every test writes a temporary app_settings.yaml and passes it via settings_path.
Run: venv/bin/python -m pytest tests/test_plugin_config.py -v
"""

from pathlib import Path

import pytest
import yaml

from core.config.plugin_config import PluginConfigError, load_plugin_config, reset_cache

SCHEMA = {
    "type": "object",
    "properties": {
        "endpoint": {"type": "string"},
        "timeout_s": {"type": "number", "default": 3.0},
        "retries": {"type": "integer", "minimum": 0, "default": 2},
    },
    "required": ["endpoint"],
}


@pytest.fixture(autouse=True)
def _fresh_cache():
    """Each test reads its own settings file."""
    reset_cache()
    yield
    reset_cache()


def _settings(tmp_path: Path, content: dict) -> Path:
    p = tmp_path / "app_settings.yaml"
    p.write_text(yaml.dump(content))
    return p


def _with_plugin(tmp_path: Path, cfg: dict) -> Path:
    return _settings(tmp_path, {"plugins": {"demo": cfg}})


def test_valid_config_with_defaults_applied(tmp_path):
    cfg = load_plugin_config("demo", SCHEMA, _with_plugin(tmp_path, {"endpoint": "http://x"}))
    assert cfg == {"endpoint": "http://x", "timeout_s": 3.0, "retries": 2}


def test_declared_values_override_defaults(tmp_path):
    cfg = load_plugin_config("demo", SCHEMA,
                             _with_plugin(tmp_path, {"endpoint": "e", "retries": 5}))
    assert cfg["retries"] == 5


def test_missing_required_key_raises(tmp_path):
    with pytest.raises(PluginConfigError, match="endpoint"):
        load_plugin_config("demo", SCHEMA, _with_plugin(tmp_path, {"retries": 1}))


def test_absent_section_with_required_key_raises(tmp_path):
    with pytest.raises(PluginConfigError, match="endpoint"):
        load_plugin_config("demo", SCHEMA, _settings(tmp_path, {"plugins": {}}))


def test_absent_section_without_required_uses_defaults(tmp_path):
    schema = {"type": "object", "properties": {"k": {"type": "integer", "default": 7}}}
    assert load_plugin_config("demo", schema, _settings(tmp_path, {})) == {"k": 7}


def test_unknown_key_is_rejected(tmp_path):
    with pytest.raises(PluginConfigError, match="typo"):
        load_plugin_config("demo", SCHEMA,
                           _with_plugin(tmp_path, {"endpoint": "e", "typo": 1}))


def test_schema_may_allow_extra_keys(tmp_path):
    schema = dict(SCHEMA, additionalProperties=True)
    cfg = load_plugin_config("demo", schema,
                             _with_plugin(tmp_path, {"endpoint": "e", "extra": 1}))
    assert cfg["extra"] == 1


def test_wrong_type_raises_and_is_never_coerced(tmp_path):
    with pytest.raises(PluginConfigError, match="retries"):
        load_plugin_config("demo", SCHEMA,
                           _with_plugin(tmp_path, {"endpoint": "e", "retries": "5"}))


def test_constraint_violation_raises(tmp_path):
    with pytest.raises(PluginConfigError, match="retries"):
        load_plugin_config("demo", SCHEMA,
                           _with_plugin(tmp_path, {"endpoint": "e", "retries": -1}))


def test_no_settings_declared_and_none_configured(tmp_path):
    assert load_plugin_config("demo", {}, _settings(tmp_path, {})) == {}


def test_no_settings_declared_but_section_configured_raises(tmp_path):
    with pytest.raises(PluginConfigError, match="declares no settings"):
        load_plugin_config("demo", {}, _with_plugin(tmp_path, {"x": 1}))


def test_invalid_schema_raises(tmp_path):
    bad = {"type": "object", "properties": {"k": {"type": "not-a-type"}}}
    with pytest.raises(PluginConfigError, match="invalid config schema"):
        load_plugin_config("demo", bad, _with_plugin(tmp_path, {"k": 1}))


def test_settings_file_absent_uses_defaults(tmp_path):
    schema = {"type": "object", "properties": {"k": {"type": "integer", "default": 1}}}
    assert load_plugin_config("demo", schema, tmp_path / "missing.yaml") == {"k": 1}
