"""
tests/test_dev_status.py (39.5.1 1a.4 G14, 1f item 5)

dev status lists every plugin directory with its install state (the plugin
detection dev pull and dev sync rely on) and reports environment problems.

AUTHOR: Deepak Panigrahy
"""

from core.cli import dev_status as ds


def _engine(tmp_path, names):
    for dist, folder in names:
        d = tmp_path / folder
        d.mkdir()
        (d / "pyproject.toml").write_text('[project]\nname = "%s"\nversion = "1.0"\n' % dist)
    return tmp_path


def test_plugin_detection_installed_and_missing(tmp_path, monkeypatch):
    root = _engine(tmp_path, [("alems-plugin-present", "alems-plugin-present"),
                              ("alems-plugin-absent", "alems-plugin-absent")])
    import importlib.metadata as md

    def fake_version(name):
        if name == "alems-plugin-present":
            return "1.0"
        raise md.PackageNotFoundError(name)
    monkeypatch.setattr(md, "version", fake_version)
    assert ds._plugins(root) == [("alems-plugin-absent", False), ("alems-plugin-present", True)]


def test_real_engine_status_renders():
    s = ds.collect_status()
    lines = ds.format_status(s)
    assert any(l.startswith("  User site:") for l in lines)
    assert any(l.startswith("  alems_sdk:") for l in lines)
    assert any(l.strip() == "Plugins:" for l in lines)
