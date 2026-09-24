# scripts/tools/audit/rebaseline.py
# Implements: alems dev audit rebaseline --reason "text" [--force]
# Creates a new baseline version. Saves change_report.json and HTML.
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import yaml

from scripts.tools.audit.utils import AUDIT_DIR, REPORTS_DIR, diff_dumps, load_ownership


def _host() -> str:
    import socket
    return socket.gethostname()


def _latest_version(host: str) -> Optional[str]:
    baselines_dir = AUDIT_DIR / host / "baselines"
    if not baselines_dir.exists():
        return None
    nums = []
    for d in baselines_dir.iterdir():
        if d.is_dir() and d.name.startswith("v"):
            try:
                nums.append((int(d.name[1:]), d.name))
            except ValueError:
                pass
    return max(nums)[1] if nums else None


def cmd_rebaseline(argv: List[str]) -> int:
    """
    Entry point for: alems dev audit rebaseline --reason "text" [--force]

    Without --force: runs check first; aborts if unexpected changes found.
    With --force: captures regardless of check result.
    Always saves change_report.json and HTML between old and new version.
    """
    host = _host()
    reason: Optional[str] = None
    force = False

    i = 0
    while i < len(argv):
        if argv[i] == "--reason" and i + 1 < len(argv):
            reason = argv[i + 1]
            i += 2
        elif argv[i] == "--force":
            force = True
            i += 1
        else:
            i += 1

    if not reason:
        print("ERROR: --reason is required", file=sys.stderr)
        print("Usage: alems dev audit rebaseline --reason \"describe the change\"",
              file=sys.stderr)
        return 1

    old_version = _latest_version(host)

    if not force and old_version:
        print("[audit] running check before rebaseline ...")
        from scripts.tools.audit.check import cmd_check
        rc = cmd_check([])
        if rc != 0:
            print("[audit] check found unexpected changes.", file=sys.stderr)
            print("[audit] if this change is intentional use --force.", file=sys.stderr)
            return 1

    from scripts.tools.audit.capture import cmd_capture
    rc = cmd_capture(["--reason", reason])
    if rc != 0:
        return rc

    new_version = _latest_version(host)
    if old_version and new_version and old_version != new_version:
        _write_change_report(host, old_version, new_version, reason)

    return 0


def _write_change_report(host: str, old_ver: str, new_ver: str, reason: str) -> None:
    """
    Write JSON and HTML change report between two baseline versions.
    This is the permanent audit trail for paper submissions.
    """
    old_path = AUDIT_DIR / host / "baselines" / old_ver / "dump.json"
    new_path = AUDIT_DIR / host / "baselines" / new_ver / "dump.json"
    if not old_path.exists() or not new_path.exists():
        return

    try:
        with open(old_path) as fh:
            old_dump = json.load(fh)
        with open(new_path) as fh:
            new_dump = json.load(fh)
        ownership = load_ownership()
        result = diff_dumps(old_dump, new_dump, ownership)
    except Exception as exc:
        print(f"[audit] WARNING: could not write change report: {exc}")
        return

    ts = datetime.now(timezone.utc).isoformat()
    report = {
        "old_version": old_ver,
        "new_version": new_ver,
        "reason": reason,
        "generated_at": ts,
        "changed_count": len(result["changed"]),
        "unchanged_count": len(result["unchanged"]),
        "changed": result["changed"][:500],
        "unchanged": result["unchanged"],
    }

    report_path = AUDIT_DIR / host / "baselines" / new_ver / "change_report.json"
    with open(report_path, "w") as fh:
        json.dump(report, fh, indent=2, default=str, sort_keys=True)

    # HTML version in reports/.
    _write_html_change_report(result, old_ver, new_ver, reason, host)

    print(f"[audit] change report: {report_path}")
    print(f"[audit] {len(result['changed'])} changed, "
          f"{len(result['unchanged'])} unchanged")


def _write_html_change_report(result: Dict, old_ver: str, new_ver: str,
                               reason: str, host: str) -> None:
    from scripts.tools.audit.check import _write_html
    # Reuse the check HTML writer with combined version label.
    combined = f"{old_ver}_to_{new_ver}"
    p = _write_html(result, combined, host, [], "rebaseline")
    print(f"[audit] HTML change report: {p}")


# Type hint for Dict used above.
from typing import Dict
