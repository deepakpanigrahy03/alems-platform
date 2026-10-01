# tests/test_store_isolation.py
# 39.5.1b: every per store location derives from the resolved store (G27, G28).
# Run: venv/bin/python -m pytest tests/test_store_isolation.py -v
import inspect
from pathlib import Path


def _use_store(monkeypatch, path: Path) -> None:
    """Point the resolver at path through its highest env layer."""
    monkeypatch.setenv("ALEMS_STORE", str(path))


def test_locations_follow_the_store(tmp_path, monkeypatch):
    from core.storage import store_context as sc

    store = tmp_path / "sbx-a" / "experiments.db"
    store.parent.mkdir()
    _use_store(monkeypatch, store)

    assert sc.store_root() == store.parent.resolve()
    for fn, name in ((sc.baselines_dir, "baselines"), (sc.logs_dir, "logs"),
                     (sc.errors_dir, "errors"), (sc.artifacts_dir, "artifacts"),
                     (sc.archive_dir, "archive")):
        assert fn() == store.parent.resolve() / name
    assert sc.baseline_cache_path().parent == sc.baselines_dir()


def test_two_stores_never_share_baselines(tmp_path, monkeypatch):
    from core.storage import store_context as sc

    a = tmp_path / "a" / "experiments.db"
    b = tmp_path / "b" / "experiments.db"
    a.parent.mkdir()
    b.parent.mkdir()
    _use_store(monkeypatch, a)
    cache_a = sc.baseline_cache_path()
    _use_store(monkeypatch, b)
    cache_b = sc.baseline_cache_path()
    assert cache_a != cache_b


def test_cache_path_helpers_are_store_scoped(tmp_path, monkeypatch):
    from core.utils.idle_baseline import get_baseline_cache_path as core_cache

    store = tmp_path / "s" / "experiments.db"
    store.parent.mkdir()
    _use_store(monkeypatch, store)
    expected = str(store.parent.resolve() / "baselines" / "idle_baseline.json")
    assert core_cache() == expected


def test_get_latest_has_no_filesystem_fallback():
    from core.utils.baseline_manager import BaselineManager

    src = inspect.getsource(BaselineManager.get_latest)
    assert ".glob(" not in src, "JSON fallback reintroduced (G27)"


def test_runner_does_not_import_scripts():
    import core.execution.experiment_runner as runner

    src = inspect.getsource(runner)
    assert "from scripts.tools.path_loader import get_baseline_cache_path" not in src
