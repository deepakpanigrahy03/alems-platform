"""Tests for 39.5.2 WP 2a logging core. No store, no network."""

import json
import logging
import threading

import pytest

from core.observability import bind, get_context, register_extra_keys
from core.observability import extras, levels, locations, setup, sinks


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    """Isolate env and logging state per test."""
    for var in ("ALEMS_LOG_LEVEL", "ALEMS_LOG_COMPONENTS", "ALEMS_LOG_MODE",
                "ALEMS_LOG_DIR", "ALEMS_ERROR_DIR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ALEMS_DATA_ROOT", str(tmp_path / "data"))
    setup.shutdown_logging()
    yield
    setup.shutdown_logging()


def _owned():
    return [h for h in logging.getLogger().handlers if getattr(h, "_alems_obs_owned", False)]


def test_setup_idempotent():
    setup.setup_logging("cli")
    setup.setup_logging("cli")
    assert len(_owned()) == 2  # console + host file


def test_context_nesting_and_threads():
    with bind(exp_id=1, run_uid="u1"):
        with bind(stage_id="spans"):
            assert get_context()["stage_id"] == "spans"
        assert get_context()["stage_id"] is None
        seen = {}
        t = threading.Thread(target=lambda: seen.update(get_context()))
        t.start(); t.join()
        assert seen["run_uid"] is None  # new thread gets default context
    assert get_context()["run_uid"] is None


def test_unknown_context_key_rejected():
    with pytest.raises(KeyError):
        with bind(colour="red"):
            pass


def test_precedence():
    cfg = levels.resolve_config([{"mode": "quiet"}, {"mode": "verbose"}, {"level": "ERROR"}])
    assert cfg.mode == "verbose" and cfg.console_level == logging.ERROR
    assert cfg.file_level == logging.DEBUG
    cfg = levels.resolve_config([{"components": "a=INFO"}, {"components": {"a": "DEBUG"}}])
    assert cfg.components == {"a": logging.DEBUG}


def test_env_layer_below_cli(monkeypatch):
    monkeypatch.setenv("ALEMS_LOG_MODE", "debug")
    cfg = setup.setup_logging("cli", cli={"mode": "quiet"})
    assert cfg.mode == "quiet"


def test_alias_and_longest_prefix():
    comps = levels.parse_components("alems.readers=DEBUG,core.readers.rapl_reader=ERROR")
    f = levels.ComponentFilter(logging.INFO, comps)
    assert f.threshold("core.readers.perf_reader") == logging.DEBUG
    assert f.threshold("core.readers.rapl_reader") == logging.ERROR
    assert f.threshold("core.storage.resolver") == logging.INFO


def test_extra_governed():
    register_extra_keys("alems.test", ["rows"])
    before = dict(extras.COUNTERS)
    out = extras.govern("core.test.x", {"rows": 3, "secret": 1})
    assert out == {"rows": 3}
    assert extras.COUNTERS["extra_unknown_dropped"] == before["extra_unknown_dropped"] + 1
    register_extra_keys("core.test", ["a", "b"])
    out = extras.govern("core.test", {"a": 1, "b": "x" * 4000})
    assert out == {"a": 1}
    assert extras.COUNTERS["extra_truncated"] == before["extra_truncated"] + 1


def test_locations(monkeypatch, tmp_path):
    monkeypatch.setenv("ALEMS_LOG_DIR", str(tmp_path / "logs"))
    assert locations.host_log_dir() == (tmp_path / "logs").resolve()
    with pytest.raises(ValueError):
        locations.guard(locations.ENGINE_ROOT / "core")


def test_json_record_has_all_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("ALEMS_LOG_DIR", str(tmp_path / "hl"))
    setup.setup_logging("cli")
    with bind(exp_id=7, stage_id="setup"):
        logging.getLogger("core.x").info("hello %s", "w")
    for h in _owned():
        h.flush()
    line = (tmp_path / "hl" / "alems.jsonl").read_text().strip().splitlines()[-1]
    rec = json.loads(line)
    for key in ("ts", "level", "logger", "msg", "pid", "extra") + tuple(
            __import__("core.observability.context", fromlist=["FIELDS"]).FIELDS):
        assert key in rec
    assert rec["msg"] == "hello w" and rec["exp_id"] == 7 and rec["host"]


def test_run_buffer_no_io_until_flush(tmp_path):
    store = tmp_path / "sb" / "experiments.db"
    setup.setup_logging("run")
    with bind(run_uid="abc"):
        logging.getLogger("core.y").info("inside")
    logging.getLogger("core.y").info("no uid")
    assert not (tmp_path / "sb" / "logs").exists()
    before = sinks.RUN_COUNTERS["run_records_unkeyed"]
    assert setup.flush_run_log(str(store)) == 1
    assert sinks.RUN_COUNTERS["run_records_unkeyed"] == before + 1
    rec = json.loads((tmp_path / "sb" / "logs" / "run_abc.jsonl").read_text())
    assert rec["msg"] == "inside" and rec["run_uid"] == "abc"


def test_run_mode_has_no_file_handler():
    setup.setup_logging("run")
    assert not any(isinstance(h, logging.FileHandler) for h in _owned())


def test_normal_console_is_warning():
    cfg = setup.setup_logging("cli")
    assert cfg.console_level == logging.WARNING


def test_config_hash_stable():
    a = levels.resolve_config([{"components": "b=INFO,a=DEBUG"}])
    b = levels.resolve_config([{"components": "a=DEBUG,b=INFO"}])
    assert levels.config_hash(a) == levels.config_hash(b)
