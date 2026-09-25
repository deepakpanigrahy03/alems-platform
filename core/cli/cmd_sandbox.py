"""
core/cli/cmd_sandbox.py

Sandbox subcommand handler for the alems CLI.
Registered as 'sandbox' in core/cli/main.py.

Commands:
    alems sandbox create <dir>              create a new sandbox repo
    alems sandbox create <dir> --adopt <store-path>   adopt an existing store
    alems sandbox use <dir>                 set active sandbox for this user
    alems sandbox use --clear               clear active sandbox
    alems sandbox info                      show current sandbox info
    alems sandbox doctor                    run health checks
    alems sandbox upgrade --dry-run         show what upgrade would do (no changes)

Design ref: DESIGN_CHUNK39_v4 sections 7.4 to 7.10.
Rule S: no new measurement capability. Plumbing only.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import sqlite3
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional

import yaml

logger = logging.getLogger(__name__)

# Canonical round-trip YAML dump: stable key order, no aliases.
# This ensures minimal git diffs when tooling rewrites sandbox files.
def _yaml_dump(data: dict) -> str:
    """Dump dict as canonical YAML. Stable key order, no aliases."""
    return yaml.dump(
        data,
        default_flow_style=False,
        sort_keys=True,
        allow_unicode=True,
    )


def handle_sandbox(argv: List[str]) -> int:
    """
    Dispatch alems sandbox <subcommand>.
    Returns exit code.
    """
    if not argv or argv[0] in ("-h", "--help"):
        _print_help()
        return 0

    sub = argv[0]
    rest = argv[1:]

    dispatch = {
        "create":  _cmd_create,
        "info":    _cmd_info,
        "doctor":  _cmd_doctor,
        "upgrade": _cmd_upgrade,
        "init":    _cmd_init,
    }

    fn = dispatch.get(sub)
    if fn is None:
        print(f"alems sandbox: unknown subcommand '{sub}'", file=sys.stderr)
        _print_help()
        return 1

    return fn(rest)


# ---------------------------------------------------------------------------
# sandbox create
# ---------------------------------------------------------------------------

def _cmd_create(argv: List[str]) -> int:
    """
    Create a new sandbox repo at <dir>.
    With --adopt <store-path>: adopt an existing store instead of creating a new one.
    """
    if not argv:
        print("usage: alems sandbox create <dir> [--adopt <store-path>]",
              file=sys.stderr)
        return 1

    target = Path(argv[0]).expanduser().resolve()
    adopt_path: Optional[Path] = None

    data_root: Optional[str] = None
    i = 1
    while i < len(argv):
        if argv[i] == "--adopt" and i + 1 < len(argv):
            adopt_path = Path(argv[i + 1]).expanduser().resolve()
            i += 2
        elif argv[i] == "--data-root" and i + 1 < len(argv):
            data_root = argv[i + 1]
            i += 2
        else:
            print(f"unknown option: {argv[i]}", file=sys.stderr)
            return 1

    # Resolve data root: --data-root flag > ALEMS_DATA_ROOT in ~/.alemsrc
    _source_alemsrc()
    if data_root is None:
        data_root = os.environ.get("ALEMS_DATA_ROOT")
    if data_root is None:
        print("error: --data-root is required or set ALEMS_DATA_ROOT in ~/.alemsrc",
              file=sys.stderr)
        print("  example: alems sandbox create <dir> --data-root /mnt/alems-data",
              file=sys.stderr)
        print("  tip:     add 'export ALEMS_DATA_ROOT=/mnt/alems-data' to ~/.alemsrc",
              file=sys.stderr)
        return 1

    # Guard: refuse to create sandbox inside an engine directory
    engine_root = _engine_root()
    try:
        target.relative_to(engine_root)
        print(f"error: cannot create sandbox inside the engine directory",
              file=sys.stderr)
        print(f"       engine detected at: {engine_root}", file=sys.stderr)
        print(f"       create elsewhere:   alems sandbox create ~/sandboxes/myproject",
              file=sys.stderr)
        return 1
    except ValueError:
        pass  # target is not under engine_root, which is correct

    # Guard: refuse to create sandbox inside another sandbox
    p = target.parent
    for _ in range(8):
        if (p / "alems-sandbox.yaml").exists():
            print(f"error: cannot create sandbox inside another sandbox",
                  file=sys.stderr)
            print(f"       parent sandbox detected at: {p}", file=sys.stderr)
            return 1
        parent = p.parent
        if parent == p:
            break
        p = parent

    # Validate adopt path exists when given
    if adopt_path and not adopt_path.exists():
        print(f"error: store to adopt does not exist: {adopt_path}", file=sys.stderr)
        return 1

    # Create sandbox directory structure
    try:
        target.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        print(f"error: directory already exists: {target}", file=sys.stderr)
        return 1

    (target / "config").mkdir()
    (target / "profiles").mkdir()
    (target / "datasets").mkdir()
    (target / "extensions").mkdir()
    (target / "reports").mkdir()

    # Generate sandbox identity
    sandbox_id = str(uuid.uuid4())
    name = target.name.lower().replace("_", "-")
    engine_version, engine_python = _resolve_engine_info()
    schema_version = 0  # resolved after store initialization below

    # Derive store path and check for collision
    _source_alemsrc()
    resolved_data_root = data_root or os.environ.get("ALEMS_DATA_ROOT", "")
    if not adopt_path:
        store_path = _derive_store_path(name, resolved_data_root)
        if store_path.exists():
            print(f"error: sandbox store already exists at:", file=sys.stderr)
            print(f"       {store_path}", file=sys.stderr)
            print(f"       choose a different name or use --adopt to work with the existing store",
                  file=sys.stderr)
            return 1

    # Write alems-sandbox.yaml (store path recorded explicitly -- never re-derived)
    manifest = {
        "sandbox_format_version": 1,
        "sandbox_id": sandbox_id,
        "name": name,
        "author": os.environ.get("USER", "unknown"),
        "created_at": date.today().isoformat(),
        "engine_version": engine_version,
        "sdk_version": f">={engine_version},<{int(engine_version.split('.')[0]) + 1}.0.0",
        "description": "",
        "extensions": [],
        "store": str(adopt_path) if adopt_path else str(store_path),
    }
    if adopt_path:
        manifest["adopted_store"] = str(adopt_path)

    _validate_manifest(manifest)
    (target / "alems-sandbox.yaml").write_text(_yaml_dump(manifest))

    # Write .gitignore
    (target / ".gitignore").write_text(
        "reports/\n"
        ".alems-cache/\n"
        ".sandbox-env\n"
        "*.pyc\n"
        "__pycache__/\n"
    )

    # Write config/overrides.yaml — full copy of engine app_settings.yaml
    # so researcher can tune every parameter per sandbox.
    import yaml as _yaml
    _app_settings_path = _engine_root() / "config" / "app_settings.yaml"
    _overrides_content = (
        "# Sandbox-level overrides. Never put hardware config, endpoints, or credentials here.\n"
        "# Edit any value below to override the engine default for this sandbox.\n"
    )
    if _app_settings_path.exists():
        try:
            with open(_app_settings_path) as _f:
                _overrides_content += _f.read()
        except Exception:
            pass
    (target / "config" / "overrides.yaml").write_text(_overrides_content)

    # Copy app_settings.yaml from engine config as sandbox-local copy
    import shutil as _shutil
    app_settings_src = _engine_root() / "config" / "app_settings.yaml"
    if app_settings_src.exists():
        _shutil.copy2(app_settings_src, target / "config" / "app_settings.yaml")

    # Copy example profiles from engine so researcher has a starting point
    # Copy per-sandbox config files from engine so sandbox is fully isolated.
    import shutil as _shutil
    _engine_config = _engine_root() / "config"

    # Single files
    _sandbox_config_files = [
        "models.yaml",
        "models.json",
        "tasks.yaml",
        "quality.yaml",
        "experiment_designer.yaml",
        "experiment_templates.yaml",
        "category_to_goal_type.yaml",
        "insights_rules.yaml",
        "research_insights.yaml",
        "research_questions.yaml",
        "gap_detection.yaml",
        "dashboard.yaml",
        "paths.yaml",
        "tunnel.yaml",
        "grid_intensity_2026.json",
        "country_metrics.yaml",
        "gwp_values.yaml",
        "adapters.yaml",
        "sources.yaml",
        "query_registry.yaml",
        "metric_registry.yaml",
    ]
    for _fname in _sandbox_config_files:
        _src = _engine_config / _fname
        if _src.exists():
            _shutil.copy2(_src, target / "config" / _fname)

    # Directories
    for _dirname in ["scenarios", "experiment_plans"]:
        _src = _engine_config / _dirname
        if _src.exists():
            _shutil.copytree(_src, target / "config" / _dirname, dirs_exist_ok=True)

    # experiment_configs → profiles/
    engine_profiles = _engine_config / "experiment_configs"
    if engine_profiles.exists():
        for f in engine_profiles.iterdir():
            if f.suffix == ".yaml" and f.is_file():
                _shutil.copy2(f, target / "profiles" / f.name)
        examples_src = engine_profiles / "examples"
        if examples_src.exists():
            _shutil.copytree(
                examples_src,
                target / "profiles" / "examples",
                dirs_exist_ok=True
            )

    # Insert sandbox_identity row into store
    store_path = adopt_path if adopt_path else store_path
    if not adopt_path:
        # New store: ensure parent exists
        store_path.parent.mkdir(parents=True, exist_ok=True)

    # For new stores: initialize first, then insert identity row
    if not adopt_path:
        _initialize_store(store_path)
        schema_version = _resolve_schema_version(store_path)
    else:
        schema_version = _resolve_schema_version(adopt_path)

    # Write alems.lock after store initialization so schema_version is correct
    lock = _build_lock(sandbox_id, engine_version, engine_python, schema_version)
    (target / "alems.lock").write_text(_yaml_dump(lock))

    # Generate .sandbox-env for this machine (machine-specific, not committed)
    _generate_sandbox_env(target, store_path)

    _insert_sandbox_identity(
        store_path=store_path,
        sandbox_id=sandbox_id,
        name=name,
        sandbox_path=str(target),
        engine_version=engine_version,
        engine_python=engine_python,
        adopted_from=str(adopt_path) if adopt_path else None,
    )

    action = "adopted" if adopt_path else "created"
    print(f"sandbox {action}: {target}")
    print(f"  sandbox_id:  {sandbox_id}")
    print(f"  store:       {store_path}")
    print(f"  engine:      {engine_version} ({engine_python})")
    print(f"\nNext steps:")
    print(f"  cd {target}")
    print(f"  alems run <profile-name>")
    return 0


# _cmd_use removed: sandbox is resolved from cwd, not an active pointer.
# cd into your sandbox directory before running alems commands.


# ---------------------------------------------------------------------------
# sandbox init
# ---------------------------------------------------------------------------

def _cmd_init(argv: List[str]) -> int:
    """
    Generate .sandbox-env for this machine.
    Run after cloning a sandbox repo on a new machine.
    """
    sandbox_path, manifest, lock, store_path = _load_current_sandbox()
    if manifest is None:
        print("no sandbox found. cd into a sandbox directory first.",
              file=sys.stderr)
        return 1

    _generate_sandbox_env(sandbox_path, store_path)
    print(f"generated: {sandbox_path}/.sandbox-env")
    print(f"  ALEMS_STORE: {store_path}")
    print(f"  ALEMS_SANDBOX: {sandbox_path}")
    return 0


# ---------------------------------------------------------------------------
# sandbox info
# ---------------------------------------------------------------------------

def _cmd_info(argv: List[str]) -> int:
    """Show current sandbox info."""
    sandbox_path, manifest, lock, store_path = _load_current_sandbox()
    if manifest is None:
        print("no sandbox found. cd into a sandbox directory first.", file=sys.stderr)
        return 1

    print(f"sandbox:        {sandbox_path}")
    print(f"name:           {manifest.get('name')}")
    print(f"sandbox_id:     {manifest.get('sandbox_id')}")
    print(f"engine_version: {manifest.get('engine_version')}")
    print(f"store:          {store_path}")
    print(f"lock:           runtime={lock.get('runtime_version')} "
          f"schema={lock.get('core_schema_version')}")
    adopted = manifest.get("adopted_store")
    if adopted:
        print(f"adopted_store:  {adopted}")
    return 0


# ---------------------------------------------------------------------------
# sandbox doctor
# ---------------------------------------------------------------------------

def _cmd_doctor(argv: List[str]) -> int:
    """Run health checks on the current sandbox."""
    sandbox_path, manifest, lock, store_path = _load_current_sandbox()
    if manifest is None:
        print("no sandbox found. cd into a sandbox directory first.", file=sys.stderr)
        return 1

    issues = 0

    # Check 1: manifest sandbox_id matches lock
    if manifest.get("sandbox_id") != lock.get("sandbox_id"):
        print("FAIL: sandbox_id mismatch between manifest and lock")
        issues += 1
    else:
        print("OK:   sandbox_id consistent")

    # Check 2: store exists
    if not store_path.exists():
        print(f"FAIL: store does not exist: {store_path}")
        issues += 1
    else:
        print(f"OK:   store exists: {store_path}")

    # Check 3: store WAL mode on network mount warning
    if _is_network_mount(store_path):
        print(f"WARN: store is on a network mount -- WAL mode may be unsafe")

    # Check 4: sandbox_identity row exists in store
    if store_path.exists():
        try:
            conn = sqlite3.connect(str(store_path), timeout=5.0)
            row = conn.execute(
                "SELECT sandbox_id FROM sandbox_identity WHERE sandbox_id = ?",
                (manifest["sandbox_id"],)
            ).fetchone()
            conn.close()
            if row:
                print("OK:   sandbox_identity row present in store")
            else:
                print("WARN: sandbox_identity row missing from store -- run: alems sandbox create --adopt")
        except Exception as e:
            print(f"WARN: could not check sandbox_identity: {e}")

    # Check 4b: .sandbox-env exists
    sandbox_env = sandbox_path / ".sandbox-env"
    if sandbox_env.exists():
        print("OK:   .sandbox-env present")
    else:
        print("WARN: .sandbox-env missing -- run: alems sandbox init")

    # Check 5: engine python exists
    engine_python = lock.get("python", "")
    if engine_python and Path(engine_python).exists():
        print(f"OK:   engine python: {engine_python}")
    else:
        print(f"FAIL: engine python not found: {engine_python}")
        issues += 1

    # Check 6: lock engine_version matches running engine
    running_version = _get_running_engine_version()
    if running_version and lock.get("runtime_version") != running_version:
        print(f"WARN: lock runtime_version={lock.get('runtime_version')} "
              f"but running engine={running_version}")
        print(f"      Run: alems sandbox upgrade --dry-run")

    if issues == 0:
        print("\ndoctor: all checks passed")
        return 0
    else:
        print(f"\ndoctor: {issues} failure(s)")
        return 1


# ---------------------------------------------------------------------------
# sandbox upgrade
# ---------------------------------------------------------------------------

def _cmd_upgrade(argv: List[str]) -> int:
    """
    Show what upgrade would do. With --run: apply it.
    Foundation phase: --run not yet implemented (39.2 is dry-run only).
    """
    dry_run = "--dry-run" in argv or "--run" not in argv

    sandbox_path, manifest, lock, store_path = _load_current_sandbox()
    if manifest is None:
        print("no sandbox found. cd into a sandbox directory first.", file=sys.stderr)
        return 1

    running_version = _get_running_engine_version()
    locked_version = lock.get("runtime_version")

    print(f"sandbox upgrade plan")
    print(f"  sandbox:         {sandbox_path}")
    print(f"  locked engine:   {locked_version}")
    print(f"  running engine:  {running_version}")

    if locked_version == running_version:
        print("  status:          up to date, nothing to do")
        return 0

    print(f"  action:          update lock from {locked_version} to {running_version}")
    print(f"  schema:          run pending migrations")

    if dry_run:
        print("\n(dry-run: no changes made. Pass --run to apply.)")
        return 0

    # --run path: not implemented in foundation phase
    print("error: --run not yet implemented in this phase", file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_engine_info():
    # type: () -> tuple
    """Return (version_str, python_path) for the running engine."""
    import sys as _sys
    try:
        from importlib.metadata import version as _ver
        v = _ver("alems-platform")
    except Exception:
        # Dev install: read from pyproject.toml
        try:
            root = _engine_root()
            import re
            text = (root / "pyproject.toml").read_text()
            m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
            v = m.group(1) if m else "0.0.0"
        except Exception:
            v = "0.0.0"
    return v, str(_sys.executable)


def _engine_root() -> Path:
    """Walk up from this file to find pyproject.toml."""
    p = Path(__file__).resolve()
    for _ in range(8):
        p = p.parent
        if (p / "pyproject.toml").exists():
            return p
    return Path.cwd()


def _resolve_schema_version(store_path: Optional[Path]) -> int:
    """Read highest applied schema version from store, or 0."""
    if store_path is None or not store_path.exists():
        return 0
    try:
        conn = sqlite3.connect(str(store_path), timeout=5.0)
        row = conn.execute(
            "SELECT MAX(version) FROM migration_history "
            "WHERE type='schema' AND status='applied' AND version < 9000"
        ).fetchone()
        conn.close()
        return row[0] or 0
    except Exception:
        return 0


def _build_lock(sandbox_id, engine_version, engine_python, schema_version):
    # type: (str, str, str, int) -> dict
    """Build alems.lock dict."""
    install_type = "pip"
    lock = {
        "lock_format_version": 1,
        "sandbox_id": sandbox_id,
        "runtime_version": engine_version,
        "sdk_version": engine_version,   # same package in foundation phase
        "core_schema_version": schema_version,
        "python": engine_python,
        "install_type": install_type,
        "locked_at": datetime.now(timezone.utc).isoformat(),
        "plugins": [],
        "extension_schema_versions": {},
    }
    # Mark as dev install when running from a checkout
    root = _engine_root()
    if (root / ".git").exists():
        lock["install_type"] = "dev"
        lock["dev_path"] = str(root)
    return lock


def _derive_store_path(name: str, data_root: str) -> Path:
    """
    Derive store path for a new sandbox.
    <data_root>/<hostname>/sandboxes/<name>/experiments.db
    Name collision is an error, not worked around with a hash suffix.
    """
    hostname = socket.gethostname().lower()
    return Path(data_root) / hostname / "sandboxes" / name / "experiments.db"


def _source_alemsrc() -> None:
    """Source ~/.alemsrc. Duplicated here to keep cmd_sandbox self-contained."""
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
def _generate_sandbox_env(sandbox_path: Path, store_path: Path) -> None:
    """
    Generate .sandbox-env for this machine.
    Uses $ALEMS_DATA_ROOT variable reference so it works for any user
    on this machine with ALEMS_DATA_ROOT set in ~/.alemsrc.
    Machine-specific: in .gitignore, regenerated on each machine via
    alems sandbox init.
    """
    _source_alemsrc()
    data_root = os.environ.get("ALEMS_DATA_ROOT", "")
    hostname = socket.gethostname().lower()
    # Use variable reference if store is under ALEMS_DATA_ROOT
    store_str = str(store_path)
    if data_root and store_str.startswith(data_root):
        store_ref = store_str.replace(data_root, "${ALEMS_DATA_ROOT}", 1)
    else:
        store_ref = store_str

    env_content = (
        f"# .sandbox-env -- machine-generated, do not commit\n"
        f"# Generated by: alems sandbox create / alems sandbox init\n"
        f"# Sandbox: {sandbox_path.name}\n"
        f"# Regenerate on a new machine: alems sandbox init\n"
        f"#\n"
        f"# ── Auto-generated (do not edit) ──────────────────────────────\n"
        f"ALEMS_STORE={store_ref}\n"
        f"ALEMS_SANDBOX={sandbox_path}\n"
        f"#\n"
        f"# ── Per-sandbox endpoint overrides (edit these) ────────────────\n"
        f"# Uncomment and set to override machine-level ~/.alemsrc values.\n"
        f"# These are machine-specific and never committed to git.\n"
        f"#\n"
        f"# vLLM endpoint for this sandbox\n"
        f"# ALEMS_VLLM_API_URL=http://your-server:8000/v1\n"
        f"#\n"
        f"# SGLang endpoint\n"
        f"# ALEMS_SGLANG_API_URL=http://your-server:30000/v1\n"
        f"#\n"
        f"# Ollama endpoint\n"
        f"# ALEMS_OLLAMA_API_URL=http://localhost:11434\n"
        f"#\n"
        f"# OpenAI API key override\n"
        f"# OPENAI_API_KEY=sk-...\n"
        f"#\n"
        f"# Anthropic API key override\n"
        f"# ANTHROPIC_API_KEY=sk-ant-...\n"
        f"#\n"
        f"# Data root override (if this sandbox uses a different disk)\n"
        f"# ALEMS_DATA_ROOT=/mnt/other-disk\n"
    )
    (sandbox_path / ".sandbox-env").write_text(env_content)

def _initialize_store(store_path: Path) -> None:
    """
    Initialize a new sandbox store using alems dev sync.
    Same as the installer: schema + migrations + YAML seed + methodology seed.
    ALEMS_STORE redirects all path resolution to the new store.
    Baseline (idle_baselines) is measured or copied separately.
    """
    import subprocess
    store_path.parent.mkdir(parents=True, exist_ok=True)
    engine_root = _engine_root()
    env = os.environ.copy()
    env["ALEMS_STORE"] = str(store_path.resolve())
    print(f"  initializing store: {store_path.resolve()}")
    # Run each init step with ALEMS_STORE set so all scripts write to sandbox store.
    # Direct python3 calls avoid bash re-sourcing .alems-env which would override ALEMS_STORE.
    steps = [
        [sys.executable, "scripts/init_sandbox_store.py", "--path", str(store_path.resolve())],
        [sys.executable, "scripts/tools/alems_migrate.py", "--run"],
        [sys.executable, "scripts/detect_environment.py"],
        [sys.executable, "scripts/load_hardware_env.py"],
        [sys.executable, "scripts/load_configs_to_db.py"],
        [sys.executable, "scripts/seed_methodology.py"],
        [sys.executable, "scripts/seed_quality_config.py"],
        [sys.executable, "scripts/migrate_yaml_to_db.py"],
        [sys.executable, "scripts/sync_task_categories.py"],
    ]
    for step in steps:
        try:
            result = subprocess.run(
                step, env=env, timeout=120, cwd=str(engine_root),
            )
            if result.returncode != 0:
                print(f"  WARN: {step[-1]} returned {result.returncode}")
        except Exception as e:
            logger.warning("init step failed %s: %s", step[-1], e)

    # Measure idle baseline
    try:
        result = subprocess.run(
            [sys.executable, "scripts/measure_sandbox_baseline.py"],
            env=env,
            timeout=120,
            cwd=str(engine_root),
        )
    except Exception as e:
        logger.warning("baseline measurement failed: %s", e)
def _insert_sandbox_identity(
    store_path, sandbox_id, name, sandbox_path,
    engine_version, engine_python, adopted_from
):
    # type: (Path, str, str, str, str, str, Optional[str]) -> None
    """Insert or replace sandbox_identity row in the store."""
    try:
        conn = sqlite3.connect(str(store_path), timeout=10.0)
        conn.execute(
            """INSERT OR REPLACE INTO sandbox_identity
               (sandbox_id, name, sandbox_path, engine_version, engine_python,
                adopted_at, adopted_from)
               VALUES (?, ?, ?, ?, ?, datetime('now'), ?)""",
            (sandbox_id, name, sandbox_path, engine_version,
             engine_python, adopted_from)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("sandbox_identity insert failed: %s", e)
        # Non-fatal: sandbox is still usable, doctor will flag the missing row


def _load_current_sandbox():
    # type: () -> tuple
    """
    Load manifest and lock for the current sandbox.
    Returns (sandbox_path, manifest_dict, lock_dict, store_path).
    All None on failure.
    """
    sandbox_path = _resolve_sandbox_path()
    if sandbox_path is None:
        return None, None, None, None

    manifest_file = sandbox_path / "alems-sandbox.yaml"
    lock_file = sandbox_path / "alems.lock"

    if not manifest_file.exists():
        return None, None, None, None

    try:
        with open(manifest_file) as f:
            manifest = yaml.safe_load(f) or {}
        lock = {}
        if lock_file.exists():
            with open(lock_file) as f:
                lock = yaml.safe_load(f) or {}
    except Exception as e:
        logger.error("load_current_sandbox: %s", e)
        return None, None, None, None

    # store path is recorded explicitly in manifest at create time
    store_field = manifest.get("store")
    if store_field:
        store_path = Path(store_field)
    else:
        # legacy fallback for sandboxes created before this fix
        adopted = manifest.get("adopted_store")
        store_path = Path(adopted) if adopted else Path("data/experiments.db")

    return sandbox_path, manifest, lock, store_path


def _resolve_sandbox_path() -> Optional[Path]:
    """
    Find sandbox root. Priority:
      1. ALEMS_SANDBOX env var
      2. Walk up from cwd for alems-sandbox.yaml
    cd into your sandbox directory before running alems commands.
    """
    env = os.environ.get("ALEMS_SANDBOX")
    if env:
        return Path(env).expanduser().resolve()

    # Walk up from the directory where alems was invoked (not engine root)
    # scripts/alems exports ALEMS_CALLER_DIR before cd-ing to engine root
    caller_dir = os.environ.get("ALEMS_CALLER_DIR", "")
    p = Path(caller_dir).resolve() if caller_dir else Path.cwd()
    for _ in range(8):
        if (p / "alems-sandbox.yaml").exists():
            return p
        parent = p.parent
        if parent == p:
            break
        p = parent

    return None


def _active_sandbox_file() -> Path:
    """Path to per-user active sandbox marker file."""
    _source_alemsrc()
    data_root = os.environ.get("ALEMS_DATA_ROOT", "")
    hostname = socket.gethostname().lower()
    user = os.environ.get("USER", "unknown")
    if data_root:
        return Path(data_root) / hostname / "users" / user / "active-sandbox"
    return Path.home() / ".alems-active-sandbox"


def _is_network_mount(path: Path) -> bool:
    """Heuristic: check if path is on a network filesystem."""
    try:
        import subprocess
        result = subprocess.run(
            ["stat", "-f", "-c", "%T", str(path)],
            capture_output=True, text=True, timeout=2
        )
        fstype = result.stdout.strip()
        # Common network fs types
        return fstype in {"nfs", "nfs4", "cifs", "smb", "fuse.sshfs"}
    except Exception:
        return False


def _get_running_engine_version() -> Optional[str]:
    """Get version of the currently running engine."""
    try:
        from importlib.metadata import version as _ver
        return _ver("alems-platform")
    except Exception:
        pass
    try:
        import re
        root = _engine_root()
        text = (root / "pyproject.toml").read_text()
        m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        return m.group(1) if m else None
    except Exception:
        return None


def _validate_manifest(manifest: dict) -> None:
    """Basic manifest validation. Raises ValueError on violation."""
    required = ["sandbox_format_version", "sandbox_id", "name",
                "author", "created_at", "engine_version", "sdk_version"]
    for key in required:
        if key not in manifest:
            raise ValueError(f"manifest missing required key: {key}")
    if manifest["sandbox_format_version"] != 1:
        raise ValueError(
            f"unsupported sandbox_format_version: {manifest['sandbox_format_version']}"
        )
    import re
    if not re.match(r'^[a-z0-9][a-z0-9-]*$', manifest["name"]):
        raise ValueError(
            f"sandbox name must be lowercase alphanumeric with hyphens: {manifest['name']}"
        )


def _print_help() -> None:
    print("""alems sandbox -- manage A-LEMS sandboxes

Usage:
  alems sandbox create <dir>                   create new sandbox
  alems sandbox create <dir> --adopt <store>   adopt existing store
  alems sandbox init                           generate .sandbox-env for this machine
  alems sandbox info                           show sandbox in current directory
  alems sandbox doctor                         run health checks on current sandbox
  alems sandbox upgrade --dry-run              preview upgrade plan

Note: cd into your sandbox directory before running alems commands.
      Sandbox is resolved by walking up from your current directory.
      On a new machine after git clone: run alems sandbox init first.
""")
