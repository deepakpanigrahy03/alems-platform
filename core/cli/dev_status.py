"""
core/cli/dev_status.py (39.5.1 1a.4, G14)

`alems dev status` in core: one command for platform, store, git, schema,
interpreter, SDK origin, and plugins. Bash only delegates.

Application API (C-CLI rule 8): collect_status returns data; format_status
renders it. Nothing here exits.

AUTHOR: Deepak Panigrahy
"""

import re
import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from core.cli.engine_env import ENGINE_ROOT, EngineEnv, engine_environment


@dataclass
class DevStatus:
    """Everything `alems dev status` reports."""

    platform: str
    env_name: str
    store: str
    branch: str
    commit: str
    schema: Optional[int]
    env: EngineEnv
    plugins: List[Tuple[str, bool]] = field(default_factory=list)  # (dir, installed)

    @property
    def ok(self) -> bool:
        """True when the environment has no problem and every plugin is installed."""
        return self.env.ok and all(installed for _d, installed in self.plugins)


def _git(root: Path, *args: str) -> str:
    """One git value, 'unknown' when git is unavailable."""
    try:
        out = subprocess.run(["git", *args], cwd=str(root), capture_output=True,
                             text=True, timeout=5)
        return out.stdout.strip() or "unknown"
    except Exception:  # git missing or not a repository
        return "unknown"


def _platform() -> str:
    """platform_class from this engine's host facts."""
    try:
        from core.storage.resolver import resolve_hw_config
        return (resolve_hw_config() or {}).get("platform_class", "unknown")
    except Exception:
        return "unknown"


def _env_name(root: Path) -> str:
    """ALEMS_ENV from .alems-env (key=value or legacy single token)."""
    path = root / ".alems-env"
    if not path.exists():
        return "unknown"
    text = path.read_text()
    m = re.search(r"^ALEMS_ENV=(.+)$", text, re.M)
    if m:
        return m.group(1).strip()
    tokens = [l.strip() for l in text.splitlines() if l.strip() and "=" not in l and not l.startswith("#")]
    return tokens[0] if tokens else "unknown"


def _store() -> str:
    """Store the resolver chooses for the current context."""
    try:
        from core.storage.resolver import resolve_store
        return str(resolve_store())
    except Exception as exc:
        return "unresolved (%s)" % exc


def _schema(store: str) -> Optional[int]:
    """Highest applied core schema version (adoption markers 9000+ excluded)."""
    try:
        con = sqlite3.connect("file:%s?mode=ro" % store, uri=True)
        try:
            row = con.execute("SELECT MAX(version) FROM migration_history "
                              "WHERE type='schema' AND status='applied' AND version < 9000").fetchone()
            return None if row is None or row[0] is None else int(row[0])
        finally:
            con.close()
    except Exception:
        return None


def _plugins(root: Path) -> List[Tuple[str, bool]]:
    """Every alems-plugin-* directory with whether its distribution is installed."""
    from importlib.metadata import PackageNotFoundError, version
    out = []
    for d in sorted(root.glob("alems-plugin-*")):
        project = d / "pyproject.toml"
        if not project.exists():
            continue
        m = re.search(r'^name\s*=\s*"([^"]+)"', project.read_text(), re.M)
        if not m:
            out.append((d.name, False))
            continue
        try:
            version(m.group(1))
            out.append((d.name, True))
        except PackageNotFoundError:
            out.append((d.name, False))
    return out


def collect_status(root: Path = ENGINE_ROOT) -> DevStatus:
    """Gather the status; never raises."""
    store = _store()
    return DevStatus(
        platform=_platform(), env_name=_env_name(root), store=store,
        branch=_git(root, "rev-parse", "--abbrev-ref", "HEAD"),
        commit=_git(root, "rev-parse", "--short", "HEAD"),
        schema=_schema(store), env=engine_environment(root), plugins=_plugins(root),
    )


def format_status(s: DevStatus) -> List[str]:
    """Human readable lines (the CLI prints them)."""
    lines = [
        "  Platform:  %s" % s.platform,
        "  Env:       %s" % s.env_name,
        "  Store:     %s" % s.store,
        "  Branch:    %s" % s.branch,
        "  Commit:    %s" % s.commit,
        "  Schema:    v%s" % ("unknown" if s.schema is None else s.schema),
        "  Python:    %s (%s)" % (s.env.python_version, s.env.python),
        "  User site: %s" % ("ENABLED" if s.env.user_site_enabled else "disabled"),
        "  alems_sdk: %s" % s.env.sdk_location,
        "  SDK:       metadata %s, constant %s" % (s.env.sdk_metadata_version, s.env.sdk_constant_version),
        "  Plugins:",
    ]
    for name, installed in s.plugins:
        lines.append("    %s  %s" % (name, "✓" if installed else "MISSING, run: alems dev install-plugins"))
    for problem in s.env.problems:
        lines.append("  PROBLEM:   %s" % problem)
    return lines
