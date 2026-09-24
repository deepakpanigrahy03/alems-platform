# scripts/tools/audit/capture.py
# Implements: alems dev audit capture [--reason "text"]
# Reads reference runs from live DB as-is. No ETL replay. No scratch copy.
# Writes baseline to <project>/audit/<host>/baselines/<version>/
from __future__ import annotations

import json
import sqlite3
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from scripts.tools.audit.utils import (
    AUDIT_DIR,
    OWNERSHIP_YAML,
    REPO_ROOT,
    auto_register_unknown,
    check_coverage,
    dump_all,
    get_db_path,
    load_ownership,
    table_hash,
)


def _host() -> str:
    return socket.gethostname()


def _ref_runs_yaml(host: str) -> Path:
    return REPO_ROOT / "audit" / host / "reference_runs.yaml"


def _nondeterministic_yaml(host: str) -> Path:
    return REPO_ROOT / "audit" / host / "nondeterministic.yaml"


def _load_nondeterministic(host: str) -> Dict[str, List[str]]:
    p = _nondeterministic_yaml(host)
    if not p.exists():
        return {}
    with open(p) as fh:
        return yaml.safe_load(fh) or {}


def _next_version(host: str) -> str:
    baselines_dir = AUDIT_DIR / host / "baselines"
    if not baselines_dir.exists():
        return "v1"
    nums = []
    for d in baselines_dir.iterdir():
        if d.is_dir() and d.name.startswith("v"):
            try:
                nums.append(int(d.name[1:]))
            except ValueError:
                pass
    return f"v{max(nums) + 1}" if nums else "v1"


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(REPO_ROOT), text=True,
        ).strip()
    except Exception:
        return "unknown"


def cmd_capture(argv: List[str]) -> int:
    """
    Entry point for: alems dev audit capture [--reason "text"] [--version vN]

    Reads reference runs from live DB and writes:
      <project>/audit/<host>/baselines/<version>/dump.json
      <project>/audit/<host>/baselines/<version>/hashes.json
      <project>/audit/<host>/baselines/<version>/meta.yaml
    """
    host = _host()
    reason = "initial baseline"
    forced_version: Optional[str] = None

    i = 0
    while i < len(argv):
        if argv[i] == "--reason" and i + 1 < len(argv):
            reason = argv[i + 1]
            i += 2
        elif argv[i] == "--version" and i + 1 < len(argv):
            forced_version = argv[i + 1]
            i += 2
        else:
            i += 1

    ref_yaml = _ref_runs_yaml(host)
    if not ref_yaml.exists():
        print(f"ERROR: reference_runs.yaml not found: {ref_yaml}", file=sys.stderr)
        return 1

    with open(ref_yaml) as fh:
        ref_config = yaml.safe_load(fh)
    ref_runs: List[Dict] = ref_config.get("runs", [])
    ref_run_ids = [r["run_id"] for r in ref_runs]

    if not ref_run_ids:
        print("ERROR: reference_runs.yaml has no runs", file=sys.stderr)
        return 1

    db_path = get_db_path()
    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}", file=sys.stderr)
        return 1

    version = forced_version or _next_version(host)
    baseline_dir = AUDIT_DIR / host / "baselines" / version
    baseline_dir.mkdir(parents=True, exist_ok=True)

    print(f"[audit] host={host}")
    print(f"[audit] db={db_path}")
    print(f"[audit] reference runs: {ref_run_ids}")
    print(f"[audit] baseline version: {version}")

    ownership = load_ownership()
    nondeterministic = _load_nondeterministic(host)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        violations = check_coverage(conn, ownership)
        if violations:
            print(f"[audit] AUTO-REGISTERING {len(violations)} unlisted tables/views:")
            for v in violations:
                print(f"  {v}")
            auto_register_unknown(violations)
            ownership = load_ownership()
            print(f"[audit] Review and correct owner/golden_class in table_ownership.yaml")

        print("[audit] reading reference runs from live DB ...")
        dump = dump_all(conn, ownership, ref_run_ids, nondeterministic)
    finally:
        conn.close()

    hashes: Dict[str, str] = {}
    for tname, rows in dump.items():
        if tname == "__schema__":
            continue
        hashes[tname] = table_hash(rows)

    with open(baseline_dir / "dump.json", "w") as fh:
        json.dump(dump, fh, indent=2, default=str, sort_keys=True)

    with open(baseline_dir / "hashes.json", "w") as fh:
        json.dump(hashes, fh, indent=2, sort_keys=True)

    non_empty = sum(1 for t, r in dump.items() if t != "__schema__" and r)

    meta = {
        "version": version,
        "host": host,
        "reason": reason,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "commit": _git_commit(),
        "db_path": str(db_path),
        "ref_run_ids": ref_run_ids,
        "tables_captured": non_empty,
        "nondeterministic_excluded": nondeterministic,
    }
    with open(baseline_dir / "meta.yaml", "w") as fh:
        yaml.dump(meta, fh, default_flow_style=False)

    print(f"[audit] captured {non_empty} tables with data")
    print(f"[audit] baseline written: {baseline_dir}")
    print(f"[audit] DONE. Run 'alems dev audit check' to verify.")
    return 0
