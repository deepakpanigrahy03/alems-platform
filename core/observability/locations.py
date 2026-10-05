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


def host_log_dir() -> Optional[Path]:
    """Host log directory, or None when no data root is configured."""
    return _host_dir("ALEMS_LOG_DIR", "log")


def error_dir() -> Optional[Path]:
    """Host error directory (used from 2d), or None."""
    return _host_dir("ALEMS_ERROR_DIR", "error")


def host_dirs_configured() -> bool:
    """True when both host log and error directories resolve."""
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
        "Note: .sandbox-env sets the store (ALEMS_STORE) only, never the data root.",
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
    return guard(Path(store_path).expanduser().resolve().parent / "logs")
