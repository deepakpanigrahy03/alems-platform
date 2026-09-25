"""
core/cli/cmd_engine.py

Engine subcommand handler for the alems CLI.
Registered as 'engine' in core/cli/main.py.

Commands:
    alems engine list                   list registered engines on this host
    alems engine register <venv-path>   register an engine install

Design ref: DESIGN_CHUNK39_v4 sections 7.2, 7.3, 7.11.
engines.yaml lives at <data_root>/<hostname>/engines.yaml.
An engine is identified by its venv python path and the version
reported by that python's alems-platform package.

PyPI model: pip install alems-platform==1.0.0 into a venv,
then alems engine register <venv-path>. The venv IS the engine.
For dev installs (pre-PyPI), version is read from pyproject.toml.
"""
from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import yaml

logger = logging.getLogger(__name__)


def handle_engine(argv: List[str]) -> int:
    """Dispatch alems engine <subcommand>. Returns exit code."""
    if not argv or argv[0] in ("-h", "--help"):
        _print_help()
        return 0

    sub = argv[0]
    rest = argv[1:]

    dispatch = {
        "list":     _cmd_list,
        "register": _cmd_register,
    }

    fn = dispatch.get(sub)
    if fn is None:
        print(f"alems engine: unknown subcommand '{sub}'", file=sys.stderr)
        _print_help()
        return 1

    return fn(rest)


# ---------------------------------------------------------------------------
# engine list
# ---------------------------------------------------------------------------

def _cmd_list(argv: List[str]) -> int:
    """List all registered engines on this host."""
    engines = _load_engines_yaml()
    if not engines:
        print("no engines registered on this host")
        print(f"run: alems engine register <venv-path>")
        return 0

    print(f"engines registered on {socket.gethostname().lower()}:")
    print()
    for e in engines:
        install_type = e.get("install_type", "pip")
        dev_note = f"  dev_path: {e.get('dev_path', '')}" if install_type == "dev" else ""
        python_exists = Path(e.get("python", "")).exists()
        status = "OK" if python_exists else "MISSING"
        print(f"  version:      {e.get('version', 'unknown')}")
        print(f"  python:       {e.get('python', '')}  [{status}]")
        print(f"  install_type: {install_type}")
        if dev_note:
            print(dev_note)
        print(f"  registered:   {e.get('registered_at', '')}")
        print()
    return 0


# ---------------------------------------------------------------------------
# engine register
# ---------------------------------------------------------------------------

def _cmd_register(argv: List[str]) -> int:
    """
    Register an engine install.

    For a pip install: pass the venv root or venv python path.
      alems engine register /opt/alems/envs/1.0.0

    For the current dev checkout (no argument needed):
      alems engine register .
      alems engine register /home/dpani/mydrive/alems-platform
    """
    if not argv:
        # Default: register the currently running engine
        target_path = None
    else:
        target_path = Path(argv[0]).expanduser().resolve()

    # Resolve python path and version
    if target_path is None:
        python_path = Path(sys.executable)
        version, install_type, dev_path = _inspect_running_engine()
    else:
        python_path = _find_python_in_venv(target_path)
        if python_path is None:
            print(f"error: no python found in {target_path}", file=sys.stderr)
            print("pass a venv root (contains bin/python3) or a pyproject.toml root",
                  file=sys.stderr)
            return 1
        version, install_type, dev_path = _inspect_engine_at(python_path, target_path)

    if version is None:
        print("error: could not determine engine version", file=sys.stderr)
        return 1

    # Load existing registry and check for duplicates
    engines = _load_engines_yaml()
    for e in engines:
        if e.get("python") == str(python_path):
            print(f"engine already registered: {python_path}")
            print(f"  version: {e.get('version')}")
            return 0

    # Build entry
    entry = {
        "version":       version,
        "python":        str(python_path),
        "install_type":  install_type,
        "registered_at": datetime.now(timezone.utc).date().isoformat(),
    }
    if install_type == "dev" and dev_path:
        entry["dev_path"] = dev_path

    engines.append(entry)
    # Sort by version descending so newest is first in list output
    engines.sort(key=lambda e: e.get("version", "0.0.0"), reverse=True)

    _save_engines_yaml(engines)

    print(f"registered engine:")
    print(f"  version:      {version}")
    print(f"  python:       {python_path}")
    print(f"  install_type: {install_type}")
    if install_type == "dev" and dev_path:
        print(f"  dev_path:     {dev_path}")
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _engines_yaml_path() -> Path:
    """<data_root>/<hostname>/engines.yaml"""
    _source_alemsrc()
    data_root = os.environ.get("ALEMS_DATA_ROOT", "")
    hostname = socket.gethostname().lower()
    alems_home = Path.home() / ".alems"
    alems_home.mkdir(exist_ok=True)
    return alems_home / "engines.yaml"


def _load_engines_yaml() -> list:
    """Load engines.yaml; return empty list if absent."""
    path = _engines_yaml_path()
    if not path.exists():
        return []
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return data.get("engines", [])
    except Exception as e:
        logger.warning("load_engines_yaml failed: %s", e)
        return []


def _save_engines_yaml(engines: list) -> None:
    """Write engines.yaml with canonical YAML."""
    path = _engines_yaml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"engines": engines}
    path.write_text(
        yaml.dump(data, default_flow_style=False, sort_keys=True, allow_unicode=True)
    )


def _find_python_in_venv(path: Path) -> Optional[Path]:
    """
    Given a path that is either a venv root or a checkout root,
    return the python executable.
    Priority: path/bin/python3, path/bin/python, path itself if it is python.
    """
    if path.is_file() and "python" in path.name:
        return path
    for candidate in [path / "bin" / "python3", path / "bin" / "python"]:
        if candidate.exists():
            return candidate
    # If path is a checkout root, use the venv inside it
    for candidate in [path / "venv" / "bin" / "python3",
                      path / "venv" / "bin" / "python"]:
        if candidate.exists():
            return candidate
    return None


def _inspect_running_engine():
    # type: () -> tuple
    """Return (version, install_type, dev_path) for the running engine."""
    version = _read_version_from_python(Path(sys.executable))
    install_type, dev_path = _classify_install(Path(sys.executable))
    return version, install_type, dev_path


def _inspect_engine_at(python_path: Path, target_path: Path):
    # type: (Path, Path) -> tuple
    """Return (version, install_type, dev_path) for an engine at python_path."""
    version = _read_version_from_python(python_path)
    install_type, dev_path = _classify_install(python_path)
    # If dev_path not found via python inspection but target_path has pyproject.toml
    if install_type == "pip" and (target_path / "pyproject.toml").exists():
        install_type = "dev"
        dev_path = str(target_path)
        if version is None:
            version = _read_version_from_pyproject(target_path)
    return version, install_type, dev_path


def _read_version_from_python(python: Path) -> Optional[str]:
    """Ask python for its alems-platform version via importlib.metadata."""
    try:
        result = subprocess.run(
            [str(python), "-c",
             "from importlib.metadata import version; print(version('alems-platform'))"],
            capture_output=True, text=True, timeout=10
        )
        v = result.stdout.strip()
        return v if v else None
    except Exception:
        pass
    return None


def _read_version_from_pyproject(root: Path) -> Optional[str]:
    """Read version from pyproject.toml for dev installs."""
    try:
        import re
        text = (root / "pyproject.toml").read_text()
        m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        return m.group(1) if m else None
    except Exception:
        return None


def _classify_install(python: Path):
    # type: (Path) -> tuple
    """
    Return (install_type, dev_path).
    install_type is 'dev' when the venv's site-packages contains an editable
    install (.pth or direct link to a checkout with .git).
    """
    # Look for a .git directory alongside the venv or two levels up
    p = python.resolve()
    for _ in range(6):
        p = p.parent
        if (p / ".git").exists() and (p / "pyproject.toml").exists():
            return "dev", str(p)
    return "pip", None


def _source_alemsrc() -> None:
    """Source ~/.alemsrc. Self-contained copy to avoid circular imports."""
    alemsrc = os.path.expanduser("~/.alemsrc")
    if not os.path.exists(alemsrc):
        return
    with open(alemsrc) as f:
        for line in f:
            line = line.strip()
            if not line.startswith("export "):
                continue
            key, _, val = line[7:].partition("=")
            os.environ.setdefault(key.strip(), val.strip())


def _print_help() -> None:
    print("""alems engine -- manage A-LEMS engine installs

Usage:
  alems engine list                    list registered engines on this host
  alems engine register                register the currently running engine
  alems engine register <venv-path>    register a pip-installed engine venv
  alems engine register <checkout>     register a dev-checkout engine

Examples:
  # After: pip install alems-platform==1.1.0 into /opt/alems/envs/1.1.0
  alems engine register /opt/alems/envs/1.1.0

  # Register the current dev checkout
  alems engine register .
""")
