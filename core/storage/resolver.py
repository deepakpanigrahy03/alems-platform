"""
core/storage/resolver.py

Store resolver (39.2, section 5).

Single authoritative function for finding the project store.  Every
caller — runtime modules, ETL, GUI, alems/ package — must go through
resolve_store() instead of constructing paths themselves.

Resolution order (first match wins):
  1. explicit argument passed by the caller (--project or --store flag)
  2. ALEMS_STORE env var
  3. ALEMS_PROJECT env var (path to a project directory; store is
     <project>/data/experiments.db as declared in alems.project manifest)
  4. alems.project manifest found by walking up from cwd
  5. active project set by 'alems project use' stored in
     $ALEMS_DATA_ROOT/<host>/.alems_active_project
  6. existing path_loader layers (ALEMS_DATA_ROOT + hostname + env)
  7. hardcoded fallback data/experiments.db with a deprecation warning

Machine config (hw_config.json) resolution is in resolve_hw_config().
Both live here so there is one import point for all path resolution.

Rule EEI-2 extension (COMPLIANCE.md section 14): new code must not add
direct sqlite3.connect() calls.  Obtain a store path from resolve_store()
and open an InProcessWriter or a read-only connection; never a literal path.
"""

from __future__ import annotations

import logging
import os
import socket
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Deprecation warning issued once per process for the data/experiments.db fallback.
_fallback_warned = False

# ---------------------------------------------------------------------------
# Store resolution
# ---------------------------------------------------------------------------

def resolve_store(explicit: Optional[str] = None) -> str:
    """
    Return the absolute path to the active project store.

    Args:
        explicit: Path passed by the caller via CLI flag or API argument.
                  When provided, skips all other layers and resolves this
                  path to absolute.

    Returns:
        Absolute path string.  The file may not exist yet (project create).

    Raises:
        RuntimeError: when no layer resolves and the fallback is also absent.
    """
    # Source alemsrc so ALEMS_DATA_ROOT is in env before any layer check.
    # Import only _source_alemsrc — never get_alems_db_path (circular).
    try:
        from scripts.tools.path_loader import _source_alemsrc  # type: ignore
        _source_alemsrc()
    except Exception:
        pass

    # Layer 1: explicit argument.
    if explicit:
        return str(Path(explicit).resolve())

    # Layer 2: ALEMS_STORE env var.
    store_env = os.environ.get("ALEMS_STORE")
    if store_env:
        return str(Path(store_env).resolve())

    # Layer 3: ALEMS_PROJECT env var — project directory, store inside it.
    project_env = os.environ.get("ALEMS_PROJECT")
    if project_env:
        store = _store_from_project_dir(Path(project_env))
        if store:
            return store

    # Layer 4: walk up from cwd looking for alems.project manifest.
    manifest_store = _walk_for_manifest()
    if manifest_store:
        return manifest_store

    # Layer 5: active project file.
    active = _read_active_project()
    if active:
        store = _store_from_project_dir(Path(active))
        if store:
            return store

    # Layer 6: ALEMS_DATA_ROOT + .alems-env logic reproduced directly.
    # Never calls get_alems_db_path() — that would be circular.
    try:
        import socket as _socket
        _base = os.environ.get("ALEMS_DATA_ROOT")
        if _base:
            _repo_root = Path(__file__).parent.parent.parent
            _env_file = _repo_root / ".alems-env"
            if _env_file.exists():
                _env = "prod"
                _base_override = None
                for _line in _env_file.read_text().splitlines():
                    _line = _line.strip()
                    if "=" in _line:
                        _k, _, _v = _line.partition("=")
                        if _k.strip() == "ALEMS_ENV":
                            _env = _v.strip()
                        elif _k.strip() == "ALEMS_DATA_ROOT":
                            _base_override = _v.strip()
                    elif _line:
                        _env = _line
                _base = _base_override or _base
                _host = _socket.gethostname().lower()
                _user = os.environ.get("USER", "unknown")
                _project = _repo_root.name
                _db_name = os.environ.get("ALEMS_DB_NAME", "experiments.db")
                return f"{_base}/{_host}/envs/{_user}/{_env}/{_project}/{_db_name}"
            _host = _socket.gethostname().lower()
            _db_name = os.environ.get("ALEMS_DB_NAME", "experiments.db")
            return f"{_base}/{_host}/{_db_name}"
    except Exception as exc:
        logger.debug("resolve_store: ALEMS_DATA_ROOT layer failed: %s", exc)

    # Layer 7: hardcoded fallback — data/experiments.db relative to cwd.
    global _fallback_warned
    if not _fallback_warned:
        logger.warning(
            "resolve_store: falling back to data/experiments.db — "
            "set ALEMS_DATA_ROOT in ~/.alemsrc or run 'alems project create'."
        )
        _fallback_warned = True
    return str(Path("data/experiments.db").resolve())


def _store_from_project_dir(project_dir: Path) -> Optional[str]:
    """
    Read alems.project manifest in project_dir and return the store path.
    Falls back to <project_dir>/data/experiments.db if manifest is absent.
    """
    manifest = project_dir / "alems.project"
    if manifest.exists():
        try:
            import yaml  # type: ignore
            with open(manifest) as f:
                data = yaml.safe_load(f)
            store_rel = data.get("store_path", "data/experiments.db")
            store = Path(store_rel)
            if not store.is_absolute():
                store = project_dir / store
            return str(store.resolve())
        except Exception as exc:
            logger.warning(
                "resolve_store: failed to read manifest %s: %s", manifest, exc
            )
    # Manifest absent — assume conventional layout.
    fallback = project_dir / "data" / "experiments.db"
    if fallback.exists():
        return str(fallback.resolve())
    return None


def _walk_for_manifest() -> Optional[str]:
    """Walk from cwd upward looking for an alems.project file."""
    current = Path.cwd()
    for parent in [current, *current.parents]:
        candidate = parent / "alems.project"
        if candidate.exists():
            store = _store_from_project_dir(parent)
            if store:
                logger.debug("resolve_store: found manifest at %s", candidate)
                return store
        # Stop at filesystem root or home directory to avoid false positives.
        if parent == Path.home() or parent == parent.parent:
            break
    return None


def _read_active_project() -> Optional[str]:
    """
    Read the active project path written by 'alems project use'.

    Stored at $ALEMS_DATA_ROOT/<host>/.alems_active_project (Option A
    agreed 2026-09-24 — machine config lives under ALEMS_DATA_ROOT, never
    ~/.config, so prod and dev environments stay isolated).
    """
    base = os.environ.get("ALEMS_DATA_ROOT")
    if not base:
        return None
    host = socket.gethostname().lower()
    pointer = Path(base) / host / ".alems_active_project"
    if not pointer.exists():
        return None
    try:
        path = pointer.read_text().strip()
        if path and Path(path).is_dir():
            return path
    except OSError:
        pass
    return None


# ---------------------------------------------------------------------------
# hw_config accessor (section 9 of spec)
# ---------------------------------------------------------------------------

def resolve_hw_config() -> Path:
    """
    Return the path to hw_config.json for this machine and environment.

    Resolution order (Option A — under ALEMS_DATA_ROOT, not ~/.config):
      1. $ALEMS_DATA_ROOT/<host>/envs/<user>/<env>/<project>/hw_config.json
         (when .alems-env is present and all env vars resolve)
      2. $ALEMS_DATA_ROOT/<host>/hw_config.json
         (bare ALEMS_DATA_ROOT without .alems-env)
      3. config/hw_config.json in the repo (legacy, always exists)

    detect_hardware.py writes to both layer 1/2 (whichever resolves) AND
    config/hw_config.json until Gate F (dual-write, section 9).
    Readers use this function so they always see the most specific config.
    """
    base = os.environ.get("ALEMS_DATA_ROOT")
    if base:
        host = socket.gethostname().lower()
        # Try the env-scoped path first.
        env_scoped = _env_scoped_hw_config(base, host)
        if env_scoped and env_scoped.exists():
            return env_scoped
        # Bare machine path.
        machine_path = Path(base) / host / "hw_config.json"
        if machine_path.exists():
            return machine_path

    # Repo fallback — always present after detect_hardware runs.
    return Path("config/hw_config.json")


def hw_config_write_paths() -> list:
    """
    Return the list of paths detect_hardware.py must write hw_config.json to.

    Dual-write until Gate F: both the machine-scoped path and the repo copy
    are kept in sync so existing callers still work.
    """
    paths = [Path("config/hw_config.json")]
    base = os.environ.get("ALEMS_DATA_ROOT")
    if base:
        host = socket.gethostname().lower()
        env_scoped = _env_scoped_hw_config(base, host)
        if env_scoped:
            paths.insert(0, env_scoped)
        else:
            paths.insert(0, Path(base) / host / "hw_config.json")
    return paths


def _env_scoped_hw_config(base: str, host: str) -> Optional[Path]:
    """
    Build the env-scoped hw_config path from .alems-env if present.
    Returns None if .alems-env is absent or incomplete.
    """
    import pathlib as _pl
    try:
        # Locate .alems-env by walking up from cwd (same logic as path_loader).
        current = _pl.Path.cwd()
        env_file = None
        for parent in [current, *current.parents]:
            candidate = parent / ".alems-env"
            if candidate.exists():
                env_file = candidate
                break
            if parent == _pl.Path.home() or parent == parent.parent:
                break
        if env_file is None:
            return None

        env = "prod"
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if "=" in line:
                k, _, v = line.partition("=")
                if k.strip() == "ALEMS_ENV":
                    env = v.strip()
            elif line and env == "prod":
                env = line  # legacy single-token format

        user = os.environ.get("USER", "unknown")
        # Repo name from cwd or ALEMS_PROJECT.
        project_env = os.environ.get("ALEMS_PROJECT")
        if project_env:
            project = _pl.Path(project_env).name
        else:
            project = _pl.Path.cwd().name

        return (
            _pl.Path(base) / host / "envs" / user / env / project / "hw_config.json"
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Active project management (used by 'alems project use')
# ---------------------------------------------------------------------------

def set_active_project(project_dir: str) -> None:
    """
    Record project_dir as the active project for this machine.
    Written to $ALEMS_DATA_ROOT/<host>/.alems_active_project.
    Raises RuntimeError if ALEMS_DATA_ROOT is not set.
    """
    base = os.environ.get("ALEMS_DATA_ROOT")
    if not base:
        raise RuntimeError(
            "ALEMS_DATA_ROOT is not set — cannot record active project."
        )
    host = socket.gethostname().lower()
    pointer = Path(base) / host / ".alems_active_project"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(str(Path(project_dir).resolve()))
    logger.info("Active project set to %s", project_dir)


def clear_active_project() -> None:
    """Remove the active project pointer for this machine."""
    base = os.environ.get("ALEMS_DATA_ROOT")
    if not base:
        return
    host = socket.gethostname().lower()
    pointer = Path(base) / host / ".alems_active_project"
    try:
        pointer.unlink()
    except FileNotFoundError:
        pass
