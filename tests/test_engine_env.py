"""
tests/test_engine_env.py (39.5.1 1a.4)

engine_environment reports, never raises, and names each problem: a foreign
alems_sdk (E1), an enabled user site (G11), a version mismatch (G1).
"""

import site
import sys
import types
from pathlib import Path

from core.cli import engine_env as ee


def _fake_sdk(monkeypatch, path, const="1.0.0", meta="1.0.0"):
    mod = types.ModuleType("alems_sdk")
    mod.__file__ = str(path)
    mod.SDK_VERSION = const
    monkeypatch.setitem(sys.modules, "alems_sdk", mod)
    import importlib.metadata as md
    monkeypatch.setattr(md, "version", lambda name: meta)


def test_clean_engine_is_ok(monkeypatch, tmp_path):
    _fake_sdk(monkeypatch, tmp_path / "alems-sdk" / "src" / "alems_sdk" / "__init__.py")
    monkeypatch.setattr(site, "ENABLE_USER_SITE", False)
    env = ee.engine_environment(root=tmp_path)
    assert env.ok and env.sdk_from_engine and env.problems == []


def test_foreign_sdk_is_a_problem(monkeypatch, tmp_path):
    _fake_sdk(monkeypatch, Path("/elsewhere/alems_sdk/__init__.py"))
    monkeypatch.setattr(site, "ENABLE_USER_SITE", False)
    env = ee.engine_environment(root=tmp_path)
    assert not env.ok and any("outside this engine" in p for p in env.problems)


def test_user_site_and_mismatch_reported(monkeypatch, tmp_path):
    _fake_sdk(monkeypatch, tmp_path / "x.py", const="0.9.0", meta="1.0.0")
    monkeypatch.setattr(site, "ENABLE_USER_SITE", True)
    env = ee.engine_environment(root=tmp_path)
    assert any("user site" in p for p in env.problems)
    assert any("mismatch" in p for p in env.problems)
