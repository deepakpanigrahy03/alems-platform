# core/versioning.py
# Lock file builder for the four version axes (SPEC_39_1 section 5, D4.1).
# No writes performed here; alems lock show calls build_lock() and prints it.
# Compliance: EEI-2 extension: no direct sqlite3.connect; uses path_loader only for the DB path.
from __future__ import annotations

import hashlib
import importlib.metadata
import socket
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


# Locate the alems-platform pyproject.toml to read runtime version.
_REPO_ROOT = Path(__file__).parent.parent


def _runtime_version() -> str:
    """Read version from pyproject.toml [project] version field."""
    pyproject = _REPO_ROOT / "pyproject.toml"
    if not pyproject.exists():
        return "unknown"
    # Avoid tomllib dependency on Python 3.9 (PVC-1); parse manually.
    for line in pyproject.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("version") and "=" in stripped:
            _, _, val = stripped.partition("=")
            return val.strip().strip('"').strip("'")
    return "unknown"


def _sdk_version() -> str:
    """Import SDK version without requiring the SDK to be installed."""
    try:
        from alems_sdk.version import SDK_VERSION
        return SDK_VERSION
    except ImportError:
        return "not_installed"


def _repo_commit() -> str:
    """Current git HEAD short SHA, or 'unknown' if git unavailable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, cwd=str(_REPO_ROOT), timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


def _installed_plugins() -> List[Dict[str, Any]]:
    """
    Return metadata for every installed alems plugin distribution.

    A plugin distribution is identified by having an entry point in any
    group starting with 'alems.' (the existing pyproject.toml groups).
    """
    plugins: List[Dict[str, Any]] = []
    seen: set = set()
    for dist in importlib.metadata.distributions():
        dist_name = dist.metadata.get("Name", "")
        if dist_name in seen:
            continue
        # Check if this distribution contributes any alems.* entry points.
        eps = dist.entry_points
        has_alems_ep = any(
            getattr(ep, "group", "").startswith("alems.")
            for ep in eps
        )
        if not has_alems_ep:
            continue
        seen.add(dist_name)
        version = dist.metadata.get("Version", "unknown")
        # Content hash: SHA256 of the RECORD file if available.
        content_hash = _dist_hash(dist)
        plugins.append({
            "dist": dist_name,
            "version": version,
            "origin": "installed",
            "content_hash": content_hash,
        })
    return plugins


def _dist_hash(dist: Any) -> str:
    """SHA256 of the distribution RECORD file, or 'unavailable'."""
    try:
        record = dist.read_text("RECORD")
        if record:
            return hashlib.sha256(record.encode()).hexdigest()
    except Exception:
        pass
    return "unavailable"


def _schema_versions(db_path: str) -> Dict[str, Any]:
    """
    Read core and namespace schema versions from migration_history.

    Core schema version: max version < 9000 with source = 'core'.
    Namespace versions: max version per source for non-core sources,
    where version >= 9000 (adoption/extension markers per spec fact 4).
    """
    core_version: Optional[int] = None
    namespace_versions: Dict[str, int] = {}
    try:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT version, source FROM migration_history WHERE status = 'applied'"
            ).fetchall()
        finally:
            conn.close()

        for row in rows:
            ver = int(row["version"])
            src = row["source"] or "core"
            if src == "core" and ver < 9000:
                if core_version is None or ver > core_version:
                    core_version = ver
            elif src != "core":
                ns = src  # e.g. 'ext:output_quality'
                if ns not in namespace_versions or ver > namespace_versions[ns]:
                    namespace_versions[ns] = ver
    except Exception as exc:
        return {
            "core_schema_version": None,
            "namespace_schema_versions": {},
            "error": str(exc),
        }
    return {
        "core_schema_version": core_version,
        "namespace_schema_versions": namespace_versions,
    }


def _get_db_path() -> Optional[str]:
    """Resolve the project store path through path_loader."""
    try:
        sys.path.insert(0, str(_REPO_ROOT / "scripts" / "tools"))
        from path_loader import get_alems_db_path
        return get_alems_db_path()
    except Exception:
        return None


def build_lock() -> Dict[str, Any]:
    """
    Build the alems.lock dict for the current install and store.

    Returns a dict ready for YAML serialisation.
    Fields defined in SPEC_39_1 section 5.
    """
    db_path = _get_db_path()
    schema_info = _schema_versions(db_path) if db_path else {
        "core_schema_version": None,
        "namespace_schema_versions": {},
        "error": "could not resolve store path",
    }

    lock: Dict[str, Any] = {
        "lock_format_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "host": socket.gethostname(),
        "runtime_version": _runtime_version(),
        "sdk_version": _sdk_version(),
        "repo_commit": _repo_commit(),
        "plugins": _installed_plugins(),
        "core_schema_version": schema_info.get("core_schema_version"),
        "namespace_schema_versions": schema_info.get("namespace_schema_versions", {}),
        # Populated in 39.4 when attribution model plugin is wrapped.
        "attribution_model_versions": [],
    }
    if "error" in schema_info:
        lock["_schema_error"] = schema_info["error"]
    return lock


def print_lock() -> None:
    """Print the current lock to stdout (alems lock show)."""
    lock = build_lock()
    print(yaml.dump(lock, default_flow_style=False, sort_keys=True), end="")
