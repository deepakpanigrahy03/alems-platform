"""
Log and error locations (design 39.5.2 section 3).

Host level: <data_root>/<hostname>/log and /error, overridden by
ALEMS_LOG_DIR and ALEMS_ERROR_DIR. Per run: <store dir>/logs. Nothing is ever
written inside the engine folder (CH39-8). Data root and hostname follow
exactly what core/storage/resolver.py layer 6 uses: ALEMS_DATA_ROOT (after
~/.alemsrc is loaded) and socket.gethostname().lower().
"""

import os
import socket
from pathlib import Path
from typing import Optional, List

# core/observability/locations.py -> engine root is two levels above core.
ENGINE_ROOT = Path(__file__).resolve().parents[2]

# type: Dict[str, list]  # last resolver report for CFG-0010
_LAST_REPORT = {"items": []}  



def _data_root() -> Optional[Path]:
    """Data root from the single resolver (same source as the store), else None."""
    from core.storage.resolver import resolve_data_root
    root, _source, report = resolve_data_root()
    _LAST_REPORT["items"] = report
    return root


def _host_dir(env_var: str, leaf: str) -> Optional[Path]:
    """Resolve a host level directory: override first, then data root."""
    override = os.environ.get(env_var)
    if override:
        return guard(Path(override).expanduser())
    root = _data_root()
    if root is None:
        return None
    return guard(root / socket.gethostname().lower() / leaf)


def guard(path: Path) -> Path:
    """
    Refuse any path inside the engine root.

    Raises:
        ValueError: the path is the engine root or below it.
    """
    resolved = path.resolve()
    if resolved == ENGINE_ROOT or ENGINE_ROOT in resolved.parents:
        raise ValueError("log path inside engine root refused: %s" % resolved)
    return resolved


def store_path() -> Optional[Path]:
    """
    The store this process writes to, from the core store resolver.

    Returns None when no store resolves (commands such as doctor and help).
    """
    try:
        from core.storage.resolver import resolve_store
        found = resolve_store()
    except Exception:  # noqa: BLE001  no store is a normal state for some commands
        return None
    # resolve_store may return a path or a (path, source, ...) tuple.
    if isinstance(found, (tuple, list)):
        found = found[0] if found else None
    # Logical path, not resolve(): a store reached through a symlink (legacy
    # envs/<user>/<env>/<project>/experiments.db) keeps its own identity, so
    # its logs and errors never fall back to the shared target directory.
    return Path(found).expanduser().absolute() if found else None




def _scoped_dir(env_var: str, store_leaf: str, host_leaf: str) -> Optional[Path]:
    """
    Resolve a log or error directory: override, then beside the store, then host.

    Beside the store means the same path resolution as the database, so user,
    environment, and sandbox uniqueness follow from the store path (amendment
    of SPEC 2a step 4 and 2d D1).
    """
    override = os.environ.get(env_var)
    if override:
        return guard(Path(override).expanduser())
    store = store_path()
    if store is not None:
        return guard(store.parent / store_leaf)
    return _host_dir(env_var, host_leaf)


def host_log_dir() -> Optional[Path]:
    """Log directory for this store (<store dir>/logs), host fallback; None without a data root."""
    return _scoped_dir("ALEMS_LOG_DIR", "logs", "log")


def error_dir() -> Optional[Path]:
    """Error record directory for this store (<store dir>/errors), host fallback; None without a data root."""
    return _scoped_dir("ALEMS_ERROR_DIR", "errors", "error")


def legacy_host_dirs() -> List[Path]:
    """Former host level log and error directories (read only lookups of older records)."""
    out = []
    for leaf in ("log", "error"):
        root = _data_root()
        if root is not None:
            out.append(root / socket.gethostname().lower() / leaf)
    return out


def host_dirs_configured() -> bool:
    """
    True when log and error directories are anchored.

    Anchored means both explicit overrides are set, or a data root resolves.
    A store found without a data root (a fallback layer) never counts:
    ALEMS-CFG-0010 stays exact, no silent fallback (2d decision D7).
    """
    explicit = os.environ.get("ALEMS_LOG_DIR") and os.environ.get("ALEMS_ERROR_DIR")
    if not explicit and _data_root() is None:
        return False
    return host_log_dir() is not None and error_dir() is not None


def missing_data_root_message() -> str:
    """Actionable ALEMS-CFG-0010 text listing every supported fix."""
    _data_root()  # refresh the report so it reflects the current environment
    lines = ["ALEMS-CFG-0010 data root not configured: logs and error records have nowhere to go.",
             "Sources checked, in precedence order:"]
    lines += ["  %-34s %s" % (src, status) for src, status in _LAST_REPORT["items"]]
    lines += [
        "Fix with one of:",
        "  1. sandbox manifest      data_root: /path   in alems-sandbox.yaml",
        "  2. ~/.alemsrc            export ALEMS_DATA_ROOT=/mnt/alems-data",
        "  3. shell environment     export ALEMS_DATA_ROOT=/path/to/data",
        "  4. explicit directories  export ALEMS_LOG_DIR=/path/log ALEMS_ERROR_DIR=/path/error",
        "  5. sandbox machine file  ALEMS_DATA_ROOT=/path   in .sandbox-env",
        "Then rerun. Verify with: alems sandbox doctor",
    ]
    return "\n".join(lines)


def require_host_dirs() -> None:
    """
    Stop hard when a run has no host log or error directory (no silent fallback).

    Raises:
        AlemsError: code ALEMS-CFG-0010 with the fix options.
    """
    if host_dirs_configured():
        return
    from core.errors import AlemsError
    raise AlemsError(missing_data_root_message(), code="ALEMS-CFG-0010")


def run_log_dir(store_path: str) -> Path:
    """Per run log directory beside the store (design 7.6 layout)."""
    # Logical parent (see store_path): symlinked stores keep separate log dirs.
    return guard(Path(store_path).expanduser().absolute().parent / "logs")
