"""
tests/test_config_resolution.py

Researcher config (tasks.yaml, models.yaml) follows the same rule as the store:
run inside a sandbox -> the sandbox copy in <sandbox>/config/;
run from the engine root (no sandbox) -> the engine copy in <engine>/config/.
An explicit ALEMS_SANDBOX is honoured; the "active sandbox" fallback is not used
for config, so a run from the engine root always reads the engine files.
"""

from pathlib import Path

import core.storage.resolver as resolver
from core.storage.resolver import resolve_config_file
from core.utils.task_loader import load_tasks

ENGINE_CONFIG = Path(resolver.__file__).resolve().parents[2] / "config"


def _make_sandbox(root, with_tasks=True):
    """Minimal sandbox: manifest plus an optional config/tasks.yaml."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "alems-sandbox.yaml").write_text("name: test\n")
    (root / "config").mkdir(exist_ok=True)
    if with_tasks:
        (root / "config" / "tasks.yaml").write_text(
            "tasks:\n  - id: sandbox_only_task\n    prompt: hi\n")
    return root


def test_inside_sandbox_uses_sandbox_copy(tmp_path, monkeypatch):
    """cwd inside a sandbox (also a subfolder): sandbox config wins."""
    monkeypatch.delenv("ALEMS_SANDBOX", raising=False)
    sb = _make_sandbox(tmp_path / "sb")
    (sb / "profiles").mkdir()
    monkeypatch.chdir(sb / "profiles")
    assert resolve_config_file("tasks.yaml") == sb / "config" / "tasks.yaml"
    assert [t["id"] for t in load_tasks()] == ["sandbox_only_task"]


def test_outside_sandbox_uses_engine_copy(tmp_path, monkeypatch):
    """cwd with no sandbox above it: engine config, even if a sandbox is active."""
    monkeypatch.delenv("ALEMS_SANDBOX", raising=False)
    monkeypatch.setattr(resolver, "_read_active_project", lambda: str(tmp_path / "other"))
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.chdir(plain)
    assert resolve_config_file("tasks.yaml") == ENGINE_CONFIG / "tasks.yaml"


def test_explicit_env_sandbox_is_honoured(tmp_path, monkeypatch):
    """ALEMS_SANDBOX points at a sandbox: its copy is used from any cwd."""
    sb = _make_sandbox(tmp_path / "sb_env")
    monkeypatch.setenv("ALEMS_SANDBOX", str(sb))
    monkeypatch.chdir(tmp_path)
    assert resolve_config_file("tasks.yaml") == sb / "config" / "tasks.yaml"


def test_sandbox_without_file_falls_back_to_engine(tmp_path, monkeypatch):
    """A sandbox lacking the file reads the engine copy (logged)."""
    monkeypatch.delenv("ALEMS_SANDBOX", raising=False)
    sb = _make_sandbox(tmp_path / "sb_no_tasks", with_tasks=False)
    monkeypatch.chdir(sb)
    assert resolve_config_file("tasks.yaml") == ENGINE_CONFIG / "tasks.yaml"


def test_explicit_path_still_wins(tmp_path, monkeypatch):
    """load_tasks(config_path=...) ignores resolution entirely."""
    f = tmp_path / "t.yaml"
    f.write_text("tasks:\n  - id: explicit_task\n")
    assert [t["id"] for t in load_tasks(str(f))] == ["explicit_task"]
