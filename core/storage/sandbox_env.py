"""
Load the sandbox machine file .sandbox-env into the process environment.

.sandbox-env is the host level parameter file of a sandbox (uncommitted):
plain KEY=VALUE lines, sourceable by a shell, read here by A-LEMS, so one
setting has one name everywhere (ALEMS_LOG_DIR in the file, the shell, and
the code).

Safety: the file is parsed, never executed. Only lines of the form
NAME=value with an upper case NAME are taken; ${VAR} references expand
against the environment; quotes around the value are removed; anything else
is ignored.

Precedence: a variable already set in the shell environment wins; the file
fills only what is unset (shell, then .sandbox-env, then defaults).
"""

import os
import re
from pathlib import Path
from typing import Dict, Optional

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)=(.*)$")
_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
FILE = ".sandbox-env"


def _sandbox_dir():
    # type: () -> Optional[Path]
    """Sandbox directory: ALEMS_SANDBOX, else the nearest manifest above the cwd."""
    env = os.environ.get("ALEMS_SANDBOX")
    if env and Path(env).is_dir():
        return Path(env)
    try:
        from core.storage.resolver import _find_sandbox_dir
        found = _find_sandbox_dir()
        return Path(found) if found else None
    except Exception:  # noqa: BLE001  no sandbox is a normal state
        return None


def parse(text):
    # type: (str) -> Dict[str, str]
    """Parse KEY=VALUE lines; comments, blank and malformed lines are skipped."""
    out = {}
    for raw in text.splitlines():
        m = _LINE.match(raw)
        if not m:
            continue
        value = m.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        # ${VAR} expands against the environment and earlier lines of the file.
        value = _REF.sub(lambda r: out.get(r.group(1), os.environ.get(r.group(1), "")), value)
        out[m.group(1)] = value
    return out


def load_sandbox_env(sandbox=None):
    # type: (Optional[Path]) -> Dict[str, str]
    """
    Set unset environment variables from .sandbox-env.

    Returns:
        The variables this call set (for reporting); empty when no file.
    """
    root = Path(sandbox) if sandbox else _sandbox_dir()
    if root is None or not (root / FILE).is_file():
        return {}
    try:
        values = parse((root / FILE).read_text())
    except OSError:
        return {}
    applied = {}
    for key, value in values.items():
        if key not in os.environ and value != "":
            os.environ[key] = value
            applied[key] = value
    return applied
