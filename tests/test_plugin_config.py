"""
tests/test_plugin_config.py

Unit tests for core/config/plugin_config.py (SPEC 35F).

All tests are self-contained — they write a temporary app_settings.yaml
and pass it directly to load_plugin_config() via settings_path.
No real config file is read or modified.

Run with:
    python -m pytest tests/test_plugin_config.py -v
"""

import pytest
import yaml
from pathlib import Path
from core.config.plugin_config import (
    load_plugin_config,
    get_raw_settings,
    reset_cache,
    PluginConfigError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_settings(tmp_path: Path, content: dict) -> Path:
    """Write a dict as YAML to a temp app_settings.yaml and return its path."""
    p = tmp_path / "app_settings.yaml"
    p.write_text(yaml.dump(content))
    return p


def settings_with_plugin(tmp_path: Path, plugin_name: str, plugin_cfg: dict) -> Path:
    """Write minimal settings with one plugins.<name> section."""
    return write_settings(tmp_path, {"plugins": {plugin_name: plugin_cfg}})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_cache():
    """Reset the settings cache before each test so tests are isolated."""
    reset_cache()
    yield
    reset_cache()


SYNTHETIC_SCHEMA = {
    "mode": {"type": "str", "required": True, "default": None},
    "package_uj": {"type": "int", "required": False, "default": 1_000_000},
    "core_uj": {"type": "int", "required": False, "default": 600_000},
    "dram_uj": {"type": "int", "required": False, "default": 200_000},
}

SCORER_SCHEMA = {
    "judge_model": {"type": "str", "required": True, "default": None},
    "judge_provider": {"type": "str", "required": True, "default": None},
}


# ---------------------------------------------------------------------------
# AC-1: Plugin loads with correct values from plugins.<name> section
# ---------------------------------------------------------------------------

def test_ac1_valid_config_returned(tmp_path):
    """Config present and valid — returns correct typed dict."""
    p = settings_with_plugin(tmp_path, "synthetic", {
        "mode": "constant",
        "package_uj": 2_000_000,
        "core_uj": 1_200_000,
        "dram_uj": 400_000,
    })
    result = load_plugin_config("synthetic", SYNTHETIC_SCHEMA, settings_path=p)
    assert result["mode"] == "constant"
    assert result["package_uj"] == 2_000_000
    assert result["core_uj"] == 1_200_000
    assert result["dram_uj"] == 400_000


# ---------------------------------------------------------------------------
# AC-2: Absent section with no required keys — returns defaults
# ---------------------------------------------------------------------------

def test_ac2_absent_section_no_required_uses_defaults(tmp_path):
    """No plugins.ollama section, no required keys — defaults returned silently."""
    p = write_settings(tmp_path, {"server": {"host": "0.0.0.0"}})
    schema = {
        "timeout_ms": {"type": "int", "required": False, "default": 30_000},
    }
    result = load_plugin_config("ollama", schema, settings_path=p)
    assert result["timeout_ms"] == 30_000


# ---------------------------------------------------------------------------
# AC-3: Missing required key raises PluginConfigError naming plugin and key
# ---------------------------------------------------------------------------

def test_ac3_missing_required_key_raises(tmp_path):
    """Required key absent from present section — raises PluginConfigError."""
    p = settings_with_plugin(tmp_path, "factuality_scorer", {
        "judge_model": "gpt-4o-mini",
        # judge_provider intentionally missing
    })
    with pytest.raises(PluginConfigError) as exc_info:
        load_plugin_config("factuality_scorer", SCORER_SCHEMA, settings_path=p)
    assert "factuality_scorer" in str(exc_info.value)
    assert "judge_provider" in str(exc_info.value)


def test_ac3_absent_section_required_key_raises(tmp_path):
    """Section entirely absent but plugin has required keys — raises PluginConfigError."""
    p = write_settings(tmp_path, {})
    with pytest.raises(PluginConfigError) as exc_info:
        load_plugin_config("factuality_scorer", SCORER_SCHEMA, settings_path=p)
    assert "factuality_scorer" in str(exc_info.value)


def test_ac3_absent_section_required_key_names_all_missing(tmp_path):
    """Error message lists all missing required keys."""
    p = write_settings(tmp_path, {})
    with pytest.raises(PluginConfigError) as exc_info:
        load_plugin_config("factuality_scorer", SCORER_SCHEMA, settings_path=p)
    msg = str(exc_info.value)
    assert "judge_model" in msg
    assert "judge_provider" in msg


# ---------------------------------------------------------------------------
# AC-4: Unknown keys log warning, not error
# ---------------------------------------------------------------------------

def test_ac4_unknown_key_does_not_raise(tmp_path):
    """Unknown key in config section is ignored without error."""
    p = settings_with_plugin(tmp_path, "synthetic", {
        "mode": "constant",
        "package_uj": 1_000_000,
        "core_uj": 600_000,
        "dram_uj": 200_000,
        "this_key_does_not_exist": "surprise",
    })
    # Must not raise.
    result = load_plugin_config("synthetic", SYNTHETIC_SCHEMA, settings_path=p)
    assert "this_key_does_not_exist" not in result


# ---------------------------------------------------------------------------
# AC-5: Type mismatch raises PluginConfigError
# ---------------------------------------------------------------------------

def test_ac5_wrong_type_raises(tmp_path):
    """String value where int expected — raises PluginConfigError."""
    p = settings_with_plugin(tmp_path, "synthetic", {
        "mode": "constant",
        "package_uj": "not_an_int",
    })
    with pytest.raises(PluginConfigError) as exc_info:
        load_plugin_config("synthetic", SYNTHETIC_SCHEMA, settings_path=p)
    assert "package_uj" in str(exc_info.value)
    assert "int" in str(exc_info.value)


def test_ac5_coercible_string_int_accepted(tmp_path):
    """Quoted integer in YAML ('30000') coerced to int without error."""
    p = settings_with_plugin(tmp_path, "synthetic", {
        "mode": "constant",
        "package_uj": "1000000",
    })
    result = load_plugin_config("synthetic", SYNTHETIC_SCHEMA, settings_path=p)
    assert result["package_uj"] == 1_000_000
    assert isinstance(result["package_uj"], int)


# ---------------------------------------------------------------------------
# AC-6: Empty schema — always returns empty dict, no error
# ---------------------------------------------------------------------------

def test_ac6_empty_schema_always_ok(tmp_path):
    """Plugin with no schema declared — empty dict returned regardless of section."""
    p = write_settings(tmp_path, {})
    result = load_plugin_config("anything", {}, settings_path=p)
    assert result == {}


# ---------------------------------------------------------------------------
# AC-7: Extensions / plugins sections absent — no errors
# ---------------------------------------------------------------------------

def test_ac7_no_plugins_section_at_all(tmp_path):
    """app_settings.yaml with no plugins key — optional plugin uses defaults."""
    p = write_settings(tmp_path, {"server": {"host": "0.0.0.0"}})
    schema = {"timeout_ms": {"type": "int", "required": False, "default": 5_000}}
    result = load_plugin_config("ollama", schema, settings_path=p)
    assert result["timeout_ms"] == 5_000


def test_ac7_settings_file_absent(tmp_path):
    """No app_settings.yaml at all — optional plugin uses defaults."""
    missing = tmp_path / "nonexistent.yaml"
    schema = {"timeout_ms": {"type": "int", "required": False, "default": 5_000}}
    result = load_plugin_config("ollama", schema, settings_path=missing)
    assert result["timeout_ms"] == 5_000


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------

def test_cache_reuses_parsed_file(tmp_path):
    """get_raw_settings() returns same object on second call (cached)."""
    p = write_settings(tmp_path, {"server": {"host": "localhost"}})
    first = get_raw_settings(settings_path=p)
    second = get_raw_settings(settings_path=p)
    assert first is second


def test_reset_cache_forces_reload(tmp_path):
    """After reset_cache(), next call re-reads the file."""
    p = write_settings(tmp_path, {"server": {"host": "v1"}})
    first = get_raw_settings(settings_path=p)
    assert first["server"]["host"] == "v1"

    reset_cache()
    p.write_text(yaml.dump({"server": {"host": "v2"}}))
    second = get_raw_settings(settings_path=p)
    assert second["server"]["host"] == "v2"
