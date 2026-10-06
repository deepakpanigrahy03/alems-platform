"""Tests for the .sandbox-env loader: parsed not executed, shell wins, ${VAR} expands."""

from core.storage import sandbox_env as se


def test_parse_skips_comments_and_code():
    text = "# comment\nALEMS_LOG_DIR=/a/logs\nexport ALEMS_X='q v'\n$(rm -rf /)\nlower=1\n"
    assert se.parse(text) == {"ALEMS_LOG_DIR": "/a/logs", "ALEMS_X": "q v"}


def test_expands_references(monkeypatch):
    monkeypatch.setenv("ALEMS_DATA_ROOT", "/mnt/d")
    assert se.parse("ALEMS_STORE=${ALEMS_DATA_ROOT}/s/x.db\n") == {"ALEMS_STORE": "/mnt/d/s/x.db"}


def test_shell_wins_and_unset_filled(monkeypatch, tmp_path):
    (tmp_path / ".sandbox-env").write_text("ALEMS_LOG_DIR=/file/logs\nALEMS_ERROR_DIR=/file/errors\n")
    monkeypatch.setenv("ALEMS_LOG_DIR", "/shell/logs")
    monkeypatch.delenv("ALEMS_ERROR_DIR", raising=False)
    applied = se.load_sandbox_env(tmp_path)
    import os
    assert os.environ["ALEMS_LOG_DIR"] == "/shell/logs"
    assert os.environ["ALEMS_ERROR_DIR"] == "/file/errors"
    assert applied == {"ALEMS_ERROR_DIR": "/file/errors"}


def test_no_file_is_noop(tmp_path):
    assert se.load_sandbox_env(tmp_path) == {}
