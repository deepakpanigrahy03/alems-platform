"""
core/cli/engine_env.py (39.5.1 1a.4, G11, G14)

Facts about the interpreter and SDK this engine actually runs with, as data.
Used by `alems dev env` and by `alems dev status`. Detects the E1 failure:
an alems_sdk imported from outside this engine (for example an editable
install in the user site of another checkout).

Application API (C-CLI rule 8): returns a typed result; never prints, never exits.
"""

import site
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

# Engine root: core/cli/engine_env.py -> engine root two levels above core.
ENGINE_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class EngineEnv:
    """Snapshot of the runtime environment of this engine."""

    python: str
    python_version: str
    user_site_enabled: bool
    sdk_location: Optional[str]
    sdk_from_engine: bool
    sdk_metadata_version: Optional[str]
    sdk_constant_version: Optional[str]
    problems: List[str]

    @property
    def ok(self) -> bool:
        """True when nothing would make results depend on a foreign install."""
        return not self.problems


def engine_environment(root: Path = ENGINE_ROOT) -> EngineEnv:
    """Collect the facts; every failure becomes a named problem, never an exception."""
    problems = []  # type: List[str]
    loc = meta = const = None
    try:
        import alems_sdk  # imported here so a broken SDK is reported, not raised
        loc = str(Path(alems_sdk.__file__).resolve())
        const = getattr(alems_sdk, "SDK_VERSION", getattr(alems_sdk, "__version__", None))
        from importlib.metadata import version
        meta = version("alems-sdk")
    except Exception as exc:  # report, do not hide (DC-3)
        problems.append("alems_sdk import or metadata failed: %s" % exc)
    from_engine = loc is not None and loc.startswith(str(root.resolve()))
    if loc is not None and not from_engine:
        problems.append("alems_sdk imported from outside this engine: %s" % loc)
    if meta and const and str(meta) != str(const):
        problems.append("SDK version mismatch: metadata %s, constant %s" % (meta, const))
    if site.ENABLE_USER_SITE:
        problems.append("user site enabled (set PYTHONNOUSERSITE=1 or use python -s)")
    return EngineEnv(
        python=sys.executable,
        python_version=sys.version.split()[0],
        user_site_enabled=bool(site.ENABLE_USER_SITE),
        sdk_location=loc,
        sdk_from_engine=from_engine,
        sdk_metadata_version=meta,
        sdk_constant_version=None if const is None else str(const),
        problems=problems,
    )
