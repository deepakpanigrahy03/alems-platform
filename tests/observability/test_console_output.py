"""
Tests for 39.5.2e console output: renderer, console sink, legacy debug env.

Covers plan 39.5.2e sections 2, 3, 6, 10: progress visible in normal mode,
hidden in quiet; warnings level prefixed; detail tier indented; color only on
a TTY; renderer honours --json and quiet; A_LEMS_DEBUG mapping.
"""

import io
import json
import logging

from core.observability import levels, sinks
from core.observability.console import Console


def _rec(name, level, msg="m"):
    """Build a LogRecord without touching handlers."""
    return logging.LogRecord(name, level, __file__, 1, msg, None, None)


# ---------------------------------------------------------------- console sink


def test_progress_passes_in_normal_not_in_quiet():
    normal = sinks.ConsoleFilter(logging.WARNING, "normal")
    quiet = sinks.ConsoleFilter(logging.ERROR, "quiet")
    rec = _rec("alems.progress", logging.INFO)
    assert normal.filter(rec)
    assert not quiet.filter(rec)


def test_detail_hidden_in_normal_shown_in_verbose():
    rec = _rec("core.readers.rapl_reader", logging.INFO)
    assert not sinks.ConsoleFilter(logging.WARNING, "normal").filter(rec)
    assert sinks.ConsoleFilter(logging.INFO, "verbose").filter(rec)


def test_debug_progress_never_passes_in_normal():
    # Progress exception covers INFO and above only.
    rec = _rec("alems.progress", logging.DEBUG)
    assert not sinks.ConsoleFilter(logging.WARNING, "normal").filter(rec)


def test_formatter_layout_plain():
    fmt = sinks.ConsoleFormatter(color=False)
    assert fmt.format(_rec("alems.progress", logging.INFO, "measure  2.41 s")) == "measure  2.41 s"
    assert fmt.format(_rec("core.x", logging.WARNING, "hot")) == "WARNING  hot"
    assert fmt.format(_rec("core.readers.perf", logging.INFO, "ipc 1.8")) == "    [readers.perf] ipc 1.8"


def test_formatter_color_only_when_enabled():
    rec = _rec("core.x", logging.ERROR, "boom")
    assert "\033[" in sinks.ConsoleFormatter(color=True).format(rec)
    assert "\033[" not in sinks.ConsoleFormatter(color=False).format(rec)


def test_use_color_respects_no_color_and_pipes(monkeypatch):
    class Tty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.delenv("NO_COLOR", raising=False)
    assert sinks.use_color(Tty())
    assert not sinks.use_color(io.StringIO())
    monkeypatch.setenv("NO_COLOR", "1")
    assert not sinks.use_color(Tty())


# ---------------------------------------------------------------- renderer


def test_renderer_text_and_quiet():
    buf = io.StringIO()
    con = Console(stream=buf)
    con.kv("energy", "1.0 J", indent=1)
    con.configure(mode="quiet")
    con.line("hidden")
    con.result({"a": 1}, text="shown")
    out = buf.getvalue().splitlines()
    assert out[0].startswith("  energy")
    assert "hidden" not in buf.getvalue()
    assert out[-1] == "shown"


def test_renderer_json_emits_only_results():
    buf = io.StringIO()
    con = Console(stream=buf)
    con.configure(json_output=True)
    con.line("text")
    con.result({"run": 1})
    assert json.loads(buf.getvalue()) == {"run": 1}


def test_renderer_never_logs(caplog):
    con = Console(stream=io.StringIO())
    with caplog.at_level(logging.DEBUG):
        con.section("s")
        con.result({"x": 1}, text="t")
    assert caplog.records == []


# ---------------------------------------------------------------- legacy env


def test_legacy_debug_layer_mapping():
    layer = levels.legacy_debug_layer({"A_LEMS_DEBUG": "1", "A_LEMS_DEBUG_MODULES": "msr_reader, rapl_reader"})
    assert layer["mode"] == "debug"
    assert layer["components"] == "msr_reader=DEBUG,rapl_reader=DEBUG"
    assert levels.legacy_debug_layer({}) == {}


def test_env_layer_wins_over_legacy():
    cfg = levels.resolve_config([
        levels.legacy_debug_layer({"A_LEMS_DEBUG": "1"}),
        levels.env_layer({"ALEMS_LOG_MODE": "normal"}),
    ])
    assert cfg.mode == "normal"


def test_short_component_name_matches_last_segment():
    flt = levels.ComponentFilter(logging.INFO, {"msr_reader": logging.DEBUG})
    assert flt.threshold("core.readers.msr_reader") == logging.DEBUG
    assert flt.threshold("core.readers.rapl_reader") == logging.INFO
