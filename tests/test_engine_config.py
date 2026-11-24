"""Tests for core/serving/engine_config.py: layer precedence, no hardcoded engines."""
from core.serving import engine_config as ec


def _setup(monkeypatch, providers, traits=None):
    ec._CACHE.clear()
    monkeypatch.setattr(ec, "_providers", lambda: providers)
    monkeypatch.setattr(ec, "_plugin_traits", lambda cfg: dict(traits or {}))


def test_endpoint_from_base_url_and_plugin_traits(monkeypatch):
    _setup(monkeypatch, {"x_local": {"is_local": True, "base_url": "http://127.0.0.1:9123/v1",
                                     "serving_engine": {"type": "anyengine"}}},
           {"worker_process_pattern": "W::Core", "single_process": False})
    monkeypatch.setenv("ALEMS_SERVE_LOG_DIR", "/tmp/prov")
    cfg = ec.resolve("x_local")
    assert (cfg["endpoint"], cfg["port"]) == ("http://127.0.0.1:9123", 9123)
    assert cfg["worker_process_pattern"] == "W::Core" and cfg["provenance_log_dir"] == "/tmp/prov"


def test_models_yaml_overrides_plugin_and_endpoint(monkeypatch):
    _setup(monkeypatch, {"x_local": {"is_local": True, "base_url": "http://127.0.0.1:9123/v1",
                                     "serving_engine": {"type": "anyengine",
                                                        "endpoint": "http://127.0.0.1:9200",
                                                        "single_process": True}}},
           {"single_process": False})
    cfg = ec.resolve("x_local")
    assert cfg["port"] == 9200 and cfg["single_process"] is True


def test_env_endpoint_when_no_block_or_base_url(monkeypatch):
    _setup(monkeypatch, {"x_local": {"is_local": True, "serving_engine": {"type": "anyengine"}}})
    monkeypatch.setenv("ALEMS_ANYENGINE_ENGINE_URL", "http://127.0.0.1:9300")
    assert ec.resolve("x_local")["port"] == 9300


def test_remote_or_unconfigured_is_none(monkeypatch):
    _setup(monkeypatch, {"r": {"is_local": False, "serving_engine": {"type": "anyengine"}},
                         "n": {"is_local": True}})
    assert ec.resolve("r") is None and ec.resolve("n") is None and ec.resolve(None) is None
    assert ec.local_engines() == []


def test_serve_script_expands_env(monkeypatch):
    _setup(monkeypatch, {"x_local": {"is_local": True, "base_url": "http://127.0.0.1:9123/v1",
                                     "serving_engine": {"type": "anyengine",
                                                        "serve_script": "$ALEMS_AI_STACK/s.sh"}}})
    monkeypatch.setenv("ALEMS_AI_STACK", "/srv/stack")
    assert ec.resolve("x_local")["serve_script"] == "/srv/stack/s.sh"
