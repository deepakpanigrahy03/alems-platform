"""
core/storage/store_context.py: the single authority for per store locations.

Every per store directory is derived from the resolved store path, i.e. the
folder that holds experiments.db. Nothing per store is rebuilt from hostname,
user, environment or project names separately; two independent derivations of
the same location are how stores leaked baselines into each other (G27).

    <store folder>/
        experiments.db
        baselines/            idle baseline exports and idle_baseline.json cache
        logs/                 per store logs   (adopted by C-LOG, 39.5.2)
        errors/               per store errors (adopted by C-ERR, 39.5.2)
        artifacts/            run artifacts
        archive/              archived exports

Machine facts (hw_config, environment, engines.yaml) are not per store and
live under <data_root>/<host>/ (design 7.6); they are not served here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from core.storage.resolver import resolve_store

# Names of the per store subdirectories; part of the sandbox data layout.
BASELINES = "baselines"
LOGS = "logs"
ERRORS = "errors"
ARTIFACTS = "artifacts"
ARCHIVE = "archive"
BASELINE_CACHE_FILE = "idle_baseline.json"


def store_path(explicit: Optional[str] = None) -> Path:
    """
    Absolute path of the active store database.

    Args:
        explicit: optional store path; same meaning as resolve_store(explicit).

    Returns:
        Path to experiments.db of the active store.
    """
    return Path(resolve_store(explicit))


def store_root(explicit: Optional[str] = None) -> Path:
    """Folder that holds the active store; root of every per store location."""
    return store_path(explicit).parent


def _subdir(name: str, explicit: Optional[str], create: bool) -> Path:
    """Return <store folder>/<name>, created on request (idempotent)."""
    path = store_root(explicit) / name
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def baselines_dir(explicit: Optional[str] = None, create: bool = True) -> Path:
    """Per store directory for idle baseline exports."""
    return _subdir(BASELINES, explicit, create)


def baseline_cache_path(explicit: Optional[str] = None) -> Path:
    """Per store idle baseline cache file (inside baselines/)."""
    return baselines_dir(explicit, create=True) / BASELINE_CACHE_FILE


def logs_dir(explicit: Optional[str] = None, create: bool = True) -> Path:
    """Per store log directory."""
    return _subdir(LOGS, explicit, create)


def errors_dir(explicit: Optional[str] = None, create: bool = True) -> Path:
    """Per store error record directory."""
    return _subdir(ERRORS, explicit, create)


def artifacts_dir(explicit: Optional[str] = None, create: bool = True) -> Path:
    """Per store run artifact directory."""
    return _subdir(ARTIFACTS, explicit, create)


def archive_dir(explicit: Optional[str] = None, create: bool = True) -> Path:
    """Per store archive directory."""
    return _subdir(ARCHIVE, explicit, create)


__all__ = [
    "store_path", "store_root", "baselines_dir", "baseline_cache_path",
    "logs_dir", "errors_dir", "artifacts_dir", "archive_dir",
]
