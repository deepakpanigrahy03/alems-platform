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
from typing import Optional

# core/observability/locations.py -> engine root is two levels above core.
ENGINE_ROOT = Path(__file__).resolve().parents[2]


def _data_root() -> Optional[Path]:
    """Data root from the environment after loading ~/.alemsrc, else None."""
    try:
        from core.storage.alemsrc import load_alemsrc

        load_alemsrc()
    except Exception:  # noqa: BLE001  observability must not fail the caller
        pass
    root = os.environ.get("ALEMS_DATA_ROOT")
    return Path(root).expanduser() if root else None


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


def host_log_dir() -> Optional[Path]:
    """Host log directory, or None when no data root is configured."""
    return _host_dir("ALEMS_LOG_DIR", "log")


def error_dir() -> Optional[Path]:
    """Host error directory (used from 2d), or None."""
    return _host_dir("ALEMS_ERROR_DIR", "error")


def run_log_dir(store_path: str) -> Path:
    """Per run log directory beside the store (design 7.6 layout)."""
    return guard(Path(store_path).expanduser().resolve().parent / "logs")
