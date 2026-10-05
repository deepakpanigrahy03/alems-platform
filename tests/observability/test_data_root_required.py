"""
No silent data root fallback (39.5.2d parts E and F).

entry run stops with ALEMS-CFG-0010 listing every source checked; entry cli
reports the same text and continues; the manifest data_root wins and is
shared by logs and the store; an unreadable manifest stops resolution.
"""
import pytest

from core.errors import AlemsError
from core.observability import locations, setup
from core.storage import resolver


@pytest.fixture
def no_root(monkeypatch, tmp_path):
    """Isolated world: no data root, no overrides, no sandbox, alemsrc neutral."""
    for var in ("ALEMS_DATA_ROOT", "ALEMS_LOG_DIR", "ALEMS_ERROR_DIR", "ALEMS_SANDBOX"):
        monkeypatch.delenv(var, raising=False)
    import core.storage.alemsrc as rc
    monkeypatch.setattr(rc, "load_alemsrc", lambda *a, **k: False)
    monkeypatch.setattr(resolver, "_read_active_project", lambda: None)
    monkeypatch.setattr(resolver, "_legacy_env_override", lambda: None)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)  # no manifest above the working directory
    return monkeypatch


def test_require_raises_cfg_0010_with_report(no_root):
    with pytest.raises(AlemsError) as info:
        locations.require_host_dirs()
    text = str(info.value)
    assert info.value.code == "ALEMS-CFG-0010"
    for part in ("~/.alemsrc", "not found", "no sandbox found", "ALEMS_DATA_ROOT",
                 "data_root:", "ALEMS_LOG_DIR", ".sandbox-env", "alems sandbox doctor"):
        assert part in text


def test_broken_alemsrc_is_named(no_root):
    import core.storage.alemsrc as rc

    def broken(*a, **k):
        raise SyntaxError("bad line 3")
    no_root.setattr(rc, "load_alemsrc", broken)
    with pytest.raises(AlemsError) as info:
        locations.require_host_dirs()
    assert "failed to load: SyntaxError: bad line 3" in str(info.value)


def test_run_entry_stops_hard(no_root):
    with pytest.raises(AlemsError):
        setup.setup_logging(entry="run")


def test_cli_entry_reports_and_continues(no_root, capsys):
    setup.setup_logging(entry="cli")
    assert "ALEMS-CFG-0010" in capsys.readouterr().err


def test_explicit_dirs_satisfy(no_root, tmp_path):
    no_root.setenv("ALEMS_LOG_DIR", str(tmp_path / "log"))
    no_root.setenv("ALEMS_ERROR_DIR", str(tmp_path / "error"))
    locations.require_host_dirs()


def test_env_data_root_satisfies(no_root, tmp_path):
    no_root.setenv("ALEMS_DATA_ROOT", str(tmp_path / "root"))
    root, source, _ = resolver.resolve_data_root()
    assert root == tmp_path / "root" and source == "shell"
    locations.require_host_dirs()


def test_manifest_data_root_wins(no_root, tmp_path):
    sb = tmp_path / "sb"
    sb.mkdir()
    (sb / "alems-sandbox.yaml").write_text("name: t\ndata_root: %s\n" % (tmp_path / "m"))
    no_root.setenv("ALEMS_SANDBOX", str(sb))
    no_root.setenv("ALEMS_DATA_ROOT", str(tmp_path / "env"))
    root, source, _ = resolver.resolve_data_root()
    assert root == tmp_path / "m" and source == "manifest"
    assert locations.error_dir().parent.parent == (tmp_path / "m").resolve()


def test_unreadable_manifest_stops(no_root, tmp_path):
    sb = tmp_path / "sb"
    sb.mkdir()
    (sb / "alems-sandbox.yaml").write_text("name: [unclosed\n")
    no_root.setenv("ALEMS_SANDBOX", str(sb))
    no_root.setenv("ALEMS_DATA_ROOT", str(tmp_path / "env"))
    root, source, report = resolver.resolve_data_root()
    assert root is None and source is None
    assert any("unreadable" in status for _, status in report)
    with pytest.raises(AlemsError):
        locations.require_host_dirs()
