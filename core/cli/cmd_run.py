"""
core/cli/cmd_run.py

Run subcommand handler for the alems CLI.
Registered as 'run' in core/cli/main.py.

Usage:
    alems run retry_test
    alems run --profile profiles/retry_test.yaml

Resolution order for profile name:
    1. profiles/<name>.yaml in current sandbox
    2. profiles/<name>.yaml in engine templates
    3. Bare path if it ends in .yaml

Validates sandbox and lock before delegating to the harness.
Foundation phase: delegates to existing experiment_runner.
Span-based runner wired in 39.4.

Design ref: DESIGN_CHUNK39_v4 section 11.1, SPEC_39_00_COMMON rule S.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


def handle_run(argv: List[str]) -> int:
    """
    Dispatch alems run <profile-name> or alems run --profile <path>.
    Returns exit code.
    """
    if not argv or argv[0] in ("-h", "--help"):
        _print_help()
        return 0

    profile_path, extra_argv = _resolve_profile(argv)
    if profile_path is None:
        return 1

    if not profile_path.exists():
        print(f"error: profile not found: {profile_path}", file=sys.stderr)
        return 1

    # Validate sandbox lock before running
    lock_ok, lock_msg = _check_lock()
    if not lock_ok:
        print(f"error: {lock_msg}", file=sys.stderr)
        print("run: alems sandbox upgrade --dry-run", file=sys.stderr)
        return 1

    print(f"profile: {profile_path}")
    print(f"sandbox: {_current_sandbox_name()}")

    # Foundation phase: delegate to existing run_experiment entry point.
    # 39.4 will replace this with the span-based runner.
    return _delegate_to_runner(profile_path, extra_argv)


def _resolve_profile(argv: List[str]):
    # type: (List[str]) -> tuple
    """
    Parse argv and return (profile_path, remaining_argv).
    Returns (None, []) on error.
    """
    # --profile <path> explicit form
    if argv[0] == "--profile":
        if len(argv) < 2:
            print("error: --profile requires a path", file=sys.stderr)
            return None, []
        return Path(argv[1]).expanduser(), argv[2:]

    # Shorthand: alems run retry_test
    name = argv[0]
    rest = argv[1:]

    # Try sandbox profiles/ dir first
    sandbox_root = _find_sandbox_root()
    if sandbox_root:
        candidate = sandbox_root / "profiles" / f"{name}.yaml"
        if candidate.exists():
            return candidate, rest
        # Also try bare name if it already has .yaml
        if name.endswith(".yaml"):
            candidate2 = sandbox_root / "profiles" / name
            if candidate2.exists():
                return candidate2, rest

    # Try engine templates profiles/ dir
    engine_root = _engine_root()
    if engine_root:
        candidate = engine_root / "config" / "profiles" / f"{name}.yaml"
        if candidate.exists():
            return candidate, rest

    # Try as a direct path
    direct = Path(name)
    if direct.exists():
        return direct, rest

    print(f"error: profile '{name}' not found in sandbox profiles/ or engine templates",
          file=sys.stderr)
    print(f"  sandbox: {sandbox_root}", file=sys.stderr)
    print(f"  tried:   profiles/{name}.yaml", file=sys.stderr)
    return None, []


def _check_lock():
    # type: () -> tuple
    """
    Verify alems.lock is consistent with running engine.
    Returns (ok: bool, message: str).
    Foundation phase: warn only, do not block runs.
    """
    sandbox_root = _find_sandbox_root()
    if sandbox_root is None:
        # No sandbox: legacy mode, allow run
        return True, ""

    lock_file = sandbox_root / "alems.lock"
    if not lock_file.exists():
        # Missing lock: warn but allow in foundation phase
        logger.warning("alems.lock not found in sandbox %s -- run: alems sandbox upgrade",
                       sandbox_root)
        return True, ""

    try:
        import yaml
        with open(lock_file) as f:
            lock = yaml.safe_load(f) or {}
        locked_version = lock.get("runtime_version")
        running_version = _running_engine_version()
        if locked_version and running_version and locked_version != running_version:
            # Warn but allow in foundation phase; 39.4 will enforce
            logger.warning(
                "lock version %s does not match running engine %s",
                locked_version, running_version
            )
    except Exception as e:
        logger.warning("lock check failed: %s", e)

    return True, ""


def _delegate_to_runner(profile_path: Path, extra_argv: List[str]) -> int:
    """
    Foundation phase delegation to existing experiment_runner.
    39.4 replaces this with span-based runner.
    """
    try:
        # Load profile yaml and translate to existing argument format
        import yaml
        with open(profile_path) as f:
            profile = yaml.safe_load(f) or {}

        task_id = profile.get("task_id")
        provider = profile.get("provider")
        repetitions = profile.get("repetitions", 1)

        if not task_id or not provider:
            print(f"error: profile must have task_id and provider", file=sys.stderr)
            return 1

        # Delegate to scripts/run_experiment.py via subprocess to preserve
        # all existing argument handling and environment setup.
        # This is the minimal shim; 39.4 replaces with direct harness call.
        import subprocess
        cmd = [
            sys.executable,
            "core/execution/tests/run_experiment.py",
            "--task-id", task_id,
            "--provider", provider,
            "--repetitions", str(repetitions),
        ]

        # Forward any extra argv (for power users passing raw flags)
        cmd.extend(extra_argv)

        result = subprocess.run(cmd)
        return result.returncode

    except Exception as e:
        logger.error("run delegation failed: %s", e)
        print(f"error: {e}", file=sys.stderr)
        return 1


def _find_sandbox_root() -> Optional[Path]:
    """Walk up from cwd to find alems-sandbox.yaml."""
    p = Path.cwd()
    for _ in range(8):
        if (p / "alems-sandbox.yaml").exists():
            return p
        parent = p.parent
        if parent == p:
            break
        p = parent
    # Check active sandbox file
    try:
        from core.cli.cmd_sandbox import _active_sandbox_file
        active = _active_sandbox_file()
        if active.exists():
            text = active.read_text().strip()
            if text:
                return Path(text)
    except Exception:
        pass
    return None


def _engine_root() -> Optional[Path]:
    """Find engine root by walking up from this file."""
    p = Path(__file__).resolve()
    for _ in range(8):
        p = p.parent
        if (p / "pyproject.toml").exists():
            return p
    return None


def _running_engine_version() -> Optional[str]:
    """Version of the currently running engine."""
    try:
        from importlib.metadata import version as _ver
        return _ver("alems-platform")
    except Exception:
        pass
    try:
        import re
        root = _engine_root()
        if root:
            text = (root / "pyproject.toml").read_text()
            m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
            return m.group(1) if m else None
    except Exception:
        pass
    return None


def _current_sandbox_name() -> str:
    """Return sandbox name for display, or 'legacy' if no sandbox active."""
    root = _find_sandbox_root()
    if root is None:
        return "legacy (no sandbox)"
    manifest = root / "alems-sandbox.yaml"
    if not manifest.exists():
        return str(root)
    try:
        import yaml
        with open(manifest) as f:
            m = yaml.safe_load(f) or {}
        return m.get("name", str(root))
    except Exception:
        return str(root)


def _print_help() -> None:
    print("""alems run -- run an experiment from a profile

Usage:
  alems run <profile-name>              resolve profiles/<name>.yaml in sandbox
  alems run --profile <path>            explicit profile path

Examples:
  alems run retry_test                  runs profiles/retry_test.yaml
  alems run gsm8k_llama                 runs profiles/gsm8k_llama.yaml
  alems run --profile /abs/path.yaml    explicit path

The profile is the experiment specification (task, provider, repetitions,
measurement settings). Results go to this sandbox's experiments.db.
""")
