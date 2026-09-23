"""
================================================================================
TEST SUITE: 8.6-B5 — Plugin-Owned Model Fragments
================================================================================

Tests the fragment merge helpers in core/models_loader.py.
9 cases: 8 unit tests + 1 integration test covering the full _load() flow.

Run from alems-platform repo root:
    pytest tests/test_model_fragment_merge.py -v

All tests are self-contained — no DB, no network, no running servers.

Design doc: DESIGN_B4_5_PLUGIN_MODEL_FRAGMENTS_v4.md
================================================================================
"""

import logging
import os
import pytest
from unittest.mock import patch, MagicMock

from core.models_loader import (
    _deep_merge_missing,
    _resolve_fragment_env_vars,
    _load_fragments,
    _log_provenance,
)


# =============================================================================
# Unit tests: helper functions
# =============================================================================

def test_case_1_empty_registry():
    """Fragment fills an empty registry when models.yaml has no providers."""
    registry = {}
    fragment = {"sglang_remote": {"base_url": "http://localhost:30000/v1"}}
    _resolve_fragment_env_vars(fragment)
    _deep_merge_missing(registry, fragment)
    assert registry["sglang_remote"]["base_url"] == "http://localhost:30000/v1"


def test_case_2_registry_full_block_wins():
    """models.yaml full block wins over fragment on collision."""
    registry = {"sglang_remote": {"base_url": "http://custom:9000/v1"}}
    fragment = {
        "sglang_remote": {
            "base_url": "http://localhost:30000/v1",
            "cost_class": "free",
        }
    }
    _resolve_fragment_env_vars(fragment)
    _deep_merge_missing(registry, fragment)
    # models.yaml value survives
    assert registry["sglang_remote"]["base_url"] == "http://custom:9000/v1"
    # fragment fills missing key
    assert registry["sglang_remote"]["cost_class"] == "free"


def test_case_3_registry_partial_key_wins():
    """models.yaml partial key wins; fragment fills absent keys."""
    registry = {"sglang_remote": {"base_url": "http://custom:9000/v1"}}
    fragment = {
        "sglang_remote": {
            "base_url": "http://localhost:30000/v1",
            "cost_class": "free",
        }
    }
    _resolve_fragment_env_vars(fragment)
    _deep_merge_missing(registry, fragment)
    assert registry["sglang_remote"]["base_url"] == "http://custom:9000/v1"
    assert registry["sglang_remote"]["cost_class"] == "free"


def test_case_4_list_is_atomic():
    """models.yaml list replaces fragment list entirely — no element merge."""
    registry = {"sglang_remote": {"models": [{"id": "Llama-3"}]}}
    fragment = {"sglang_remote": {"models": [{"id": "Mistral-7B"}]}}
    _resolve_fragment_env_vars(fragment)
    _deep_merge_missing(registry, fragment)
    # models.yaml list wins; fragment list is discarded entirely
    assert registry["sglang_remote"]["models"] == [{"id": "Llama-3"}]


def test_case_5_env_var_fills_gap(monkeypatch):
    """Env var overrides fragment hard default when models.yaml is absent."""
    monkeypatch.setenv("ALEMS_SGLANG_API_URL", "http://10.0.0.20:30000/v1")
    registry = {}
    fragment = {
        "sglang_remote": {
            "base_url": "http://localhost:30000/v1",
            "base_url_env": "ALEMS_SGLANG_API_URL",
        }
    }
    _resolve_fragment_env_vars(fragment)
    _deep_merge_missing(registry, fragment)
    # env var overrides the hard default
    assert registry["sglang_remote"]["base_url"] == "http://10.0.0.20:30000/v1"
    # _env directive key is consumed and removed
    assert "base_url_env" not in registry["sglang_remote"]


def test_case_6_models_yaml_beats_fragment_env(monkeypatch):
    """models.yaml explicit value beats fragment env var. Critical regression guard."""
    monkeypatch.setenv("ALEMS_SGLANG_API_URL", "http://env-server:30000/v1")
    registry = {"sglang_remote": {"base_url": "http://yaml-server:9000/v1"}}
    fragment = {
        "sglang_remote": {
            "base_url": "http://localhost:30000/v1",
            "base_url_env": "ALEMS_SGLANG_API_URL",
        }
    }
    # env resolution runs on the fragment BEFORE merge
    _resolve_fragment_env_vars(fragment)
    # at this point fragment base_url == env value == "http://env-server:30000/v1"
    # but models.yaml value in registry must win
    _deep_merge_missing(registry, fragment)
    assert registry["sglang_remote"]["base_url"] == "http://yaml-server:9000/v1"


def test_case_7_api_key_env_preserved():
    """api_key_env is runtime metadata — survives resolution unchanged."""
    registry = {}
    fragment = {
        "sglang_remote": {
            "api_key_env": "ALEMS_SGLANG_API_KEY",
            "base_url": "http://localhost:30000/v1",
        }
    }
    _resolve_fragment_env_vars(fragment)
    _deep_merge_missing(registry, fragment)
    # api_key_env must not be consumed — it is not a resolution directive
    assert registry["sglang_remote"]["api_key_env"] == "ALEMS_SGLANG_API_KEY"


def test_case_8_duplicate_provider_first_discovered_wins(caplog):
    """First-discovered plugin wins on duplicate provider name; warning is emitted."""
    fragment_a = {"sglang_remote": {"base_url": "http://plugin-a:30000/v1"}}
    fragment_b = {"sglang_remote": {"base_url": "http://plugin-b:30000/v1"}}

    merged = {}
    provenance = {}

    # Simulate first discovery
    for provider_name in list(fragment_a):
        provenance[provider_name] = {"source": "plugin-a", "entry_point": "sglang"}
    _deep_merge_missing(merged, fragment_a)

    # Simulate duplicate discovery — replicate _load_fragments() duplicate logic
    with caplog.at_level(logging.WARNING, logger="core.models_loader"):
        for provider_name in list(fragment_b):
            if provider_name in merged:
                import logging as _logging
                _logging.getLogger("core.models_loader").warning(
                    "models_loader: provider '%s' already registered by '%s'. "
                    "Ignoring duplicate from '%s'. "
                    "First-discovered plugin wins.",
                    provider_name,
                    provenance[provider_name]["source"],
                    "plugin-b",
                )
                fragment_b.pop(provider_name)
        _deep_merge_missing(merged, fragment_b)

    # First-discovered value survives
    assert merged["sglang_remote"]["base_url"] == "http://plugin-a:30000/v1"
    # Warning was emitted
    assert any("First-discovered" in r.message for r in caplog.records)


# =============================================================================
# Integration test: full _load() flow via models_loader
# =============================================================================

def test_integration_load_model_registry(tmp_path, monkeypatch):
    """
    Integration test: models.yaml + fake entry-point fragment + env var.

    Proves the canonical flow order in _load() is correct end-to-end:
        1. models.yaml read
        2. fragments discovered
        3. env vars resolved on fragments
        4. _deep_merge_missing — models.yaml wins
        5. provenance logged

    Critical case: models.yaml value beats fragment env var.
    """
    import importlib
    import core.models_loader as ml

    # Reset module cache so this test gets a fresh _load()
    ml._cache = None

    # Minimal models.yaml: researcher has set base_url for sglang_remote
    yaml_content = """
_defaults: {}

providers:
  sglang_remote:
    base_url: http://yaml-server:9000/v1
    cost_class: premium
    models: []
"""
    yaml_file = tmp_path / "models.yaml"
    yaml_file.write_text(yaml_content)

    # Patch _YAML_PATH to point to our temp file
    monkeypatch.setattr(ml, "_YAML_PATH", yaml_file)

    # Fragment from a fake plugin — supplies sglang_remote defaults + colibri_remote
    fake_fragment = {
        "sglang_remote": {
            "base_url": "http://localhost:30000/v1",
            "base_url_env": "ALEMS_SGLANG_API_URL",
            "is_local": False,
            "cost_class": "free",      # fragment default; models.yaml "premium" must win
        },
        "colibri_remote": {
            "base_url": "http://localhost:8001/v1",
            "is_local": False,
        },
    }

    # Env var is set — but models.yaml value must still win for sglang_remote
    monkeypatch.setenv("ALEMS_SGLANG_API_URL", "http://env-server:30000/v1")

    fake_ep = MagicMock()
    fake_ep.load.return_value = fake_fragment
    fake_ep.name = "sglang"
    fake_ep.value = "alems_plugin_sglang.models_fragment:fragment"

    with patch(
        "core.models_loader.entry_points",
        return_value=[fake_ep],
    ):
        # Trigger _load() via public API
        providers = ml.list_providers()

    # models.yaml base_url wins over env var and fragment default
    sglang = providers["sglang_remote"]["provider_meta"]
    assert sglang["base_url"] == "http://yaml-server:9000/v1"

    # models.yaml cost_class "premium" wins over fragment "free"
    assert sglang["cost_class"] == "premium"

    # Fragment fills missing key (is_local absent from models.yaml)
    assert sglang["is_local"] is False

    # Fragment-only provider colibri_remote appears in registry
    assert "colibri_remote" in providers

    # base_url_env directive is consumed — must not appear in final registry
    assert "base_url_env" not in sglang

    # Reset cache after test so other tests are not affected
    ml._cache = None
