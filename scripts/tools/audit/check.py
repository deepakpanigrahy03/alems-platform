# scripts/tools/audit/check.py
# Implements: alems dev audit check [--version vN] [--html]
# Reads reference runs from live DB as-is. Diffs against baseline.
# Prints CHANGED and UNCHANGED sections. Optionally writes HTML report.
from __future__ import annotations

import json
import sqlite3
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from scripts.tools.audit.utils import (
    AUDIT_DIR,
    REPORTS_DIR,
    REPO_ROOT,
    auto_register_unknown,
    check_coverage,
    diff_dumps,
    dump_all,
    get_db_path,
    load_ownership,
    table_hash,
)


def _host() -> str:
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


def _load_nondeterministic(host: str) -> Dict[str, List[str]]:
    p = REPO_ROOT / "audit" / host / "nondeterministic.yaml"
    if not p.exists():
        return {}
    with open(p) as fh:
        return yaml.safe_load(fh) or {}


def _write_html(result: Dict, version: str, host: str,
                ref_run_ids: List[int], commit: str) -> Path:
    """
    Write a self-contained HTML audit report to <project>/reports/.
    Returns the path written.
    """
    changed = result["changed"]
    unchanged = result["unchanged"]
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    status = "PASS" if not changed else "CHANGED"
    status_color = "#2d7a2d" if not changed else "#b22222"

    # Group cell diffs by table for the report.
    by_table: Dict[str, List[Dict]] = {}
    for d in changed:
        t = d.get("table", "?")
        by_table.setdefault(t, []).append(d)

    rows_html = ""
    for table, diffs in sorted(by_table.items()):
        for d in diffs:
            kind = d.get("kind", "")
            if kind == "cell":
                rows_html += (
                    f"<tr class='changed'>"
                    f"<td>{table}</td>"
                    f"<td>{d.get('col','')}</td>"
                    f"<td>{d.get('key','')}</td>"
                    f"<td class='old'>{d.get('old','')}</td>"
                    f"<td class='new'>{d.get('new','')}</td>"
                    f"</tr>\n"
                )
            else:
                rows_html += (
                    f"<tr class='changed'>"
                    f"<td>{table}</td>"
                    f"<td colspan='4'>{kind.upper()}: "
                    f"old={d.get('old','')} new={d.get('new','')}</td>"
                    f"</tr>\n"
                )

    unchanged_html = "".join(
        f"<tr><td>{t}</td><td class='pass'>identical</td></tr>\n"
        for t in sorted(unchanged)
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>A-LEMS Audit Report {version} {ts}</title>
<style>
  body {{ font-family: monospace; margin: 2em; background: #fafafa; color: #222; }}
  h1 {{ font-size: 1.3em; }}
  .meta {{ background: #f0f0f0; padding: 1em; border-left: 4px solid #888; margin-bottom: 1.5em; }}
  .status {{ font-size: 1.5em; font-weight: bold; color: {status_color}; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 2em; }}
  th {{ background: #ddd; padding: 0.4em 0.8em; text-align: left; }}
  td {{ padding: 0.3em 0.8em; border-bottom: 1px solid #eee; }}
  tr.changed td {{ background: #fff0f0; }}
  .old {{ color: #b22222; }}
  .new {{ color: #2d7a2d; }}
  .pass {{ color: #2d7a2d; }}
  details summary {{ cursor: pointer; font-weight: bold; margin: 1em 0 0.5em; }}
</style>
</head>
<body>
<h1>A-LEMS Audit Report</h1>
<div class="meta">
  <div class="status">{status}</div>
  <br>
  <b>Baseline:</b> {version} &nbsp;
  <b>Host:</b> {host} &nbsp;
  <b>Commit:</b> {commit} &nbsp;
  <b>Generated:</b> {ts}<br>
  <b>Reference runs:</b> {ref_run_ids}<br>
  <b>Changed:</b> {len(changed)} difference(s) &nbsp;
  <b>Unchanged:</b> {len(unchanged)} table(s) identical
</div>

<details open>
<summary>CHANGED ({len(changed)} difference(s))</summary>
{"<p>No differences found.</p>" if not changed else f'''
<table>
<tr><th>Table</th><th>Column</th><th>Key</th><th>Baseline</th><th>Current</th></tr>
{rows_html}
</table>'''}
</details>

<details>
<summary>UNCHANGED ({len(unchanged)} tables identical to baseline)</summary>
<table>
<tr><th>Table</th><th>Status</th></tr>
{unchanged_html}
</table>
</details>

</body>
</html>"""

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts_file = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    out_path = REPORTS_DIR / f"audit_check_{version}_{ts_file}.html"
    with open(out_path, "w") as fh:
        fh.write(html)
    return out_path


def cmd_check(argv: List[str]) -> int:
    """
    Entry point for: alems dev audit check [--version vN] [--html]

    Reads reference runs from live DB, diffs against baseline.
    Prints CHANGED and UNCHANGED. With --html also writes reports/audit_check_*.html.
    Returns 0 if no changes, 1 if any changes found.
    """
    host = _host()
    forced_version: Optional[str] = None
    write_html = False

    i = 0
    while i < len(argv):
        if argv[i] == "--version" and i + 1 < len(argv):
            forced_version = argv[i + 1]
            i += 2
        elif argv[i] == "--html":
            write_html = True
            i += 1
        else:
            i += 1

    version = forced_version or _latest_version(host)
    if not version:
        print(f"ERROR: no baseline found under {AUDIT_DIR}/{host}/baselines/",
              file=sys.stderr)
        print("Run 'alems dev audit capture' first.", file=sys.stderr)
        return 1

    baseline_dir = AUDIT_DIR / host / "baselines" / version
    if not (baseline_dir / "dump.json").exists():
        print(f"ERROR: baseline dump missing: {baseline_dir}/dump.json",
              file=sys.stderr)
        return 1

    with open(baseline_dir / "dump.json") as fh:
        baseline_dump = json.load(fh)
    with open(baseline_dir / "hashes.json") as fh:
        baseline_hashes: Dict[str, str] = json.load(fh)
    with open(baseline_dir / "meta.yaml") as fh:
        meta = yaml.safe_load(fh)

    ref_run_ids: List[int] = meta.get("ref_run_ids", [])
    commit: str = meta.get("commit", "unknown")
    nondeterministic = _load_nondeterministic(host)
    ownership = load_ownership()

    db_path = get_db_path()
    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}", file=sys.stderr)
        return 1

    print(f"[audit] host={host}  baseline={version}  db={db_path}")
    print(f"[audit] reference runs: {ref_run_ids}")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        violations = check_coverage(conn, ownership)
        if violations:
            print(f"[audit] AUTO-REGISTERING {len(violations)} unlisted tables/views")
            auto_register_unknown(violations)
            ownership = load_ownership()
            print("[audit] FAIL: fix table_ownership.yaml before check")
            return 1

        print("[audit] reading reference runs from live DB ...")
        changed_tables: List[str] = []
        for tname in baseline_hashes:
            info = ownership.get(tname, {})
            rows = dump_all(conn, {tname: info}, ref_run_ids,
                            nondeterministic).get(tname, [])
            if table_hash(rows) != baseline_hashes[tname]:
                changed_tables.append(tname)

        schema_rows = conn.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE type IN ('table','view') ORDER BY name"
        ).fetchall()
        current_schema = [{"type": r[0], "name": r[1], "sql": r[2]}
                          for r in schema_rows]
        schema_changed = current_schema != baseline_dump.get("__schema__")

        if not changed_tables and not schema_changed:
            all_unchanged = list(baseline_hashes.keys())
            result = {"changed": [], "unchanged": all_unchanged}
            _print_report(result, version)
            if write_html:
                p = _write_html(result, version, host, ref_run_ids, commit)
                print(f"[audit] report: {p}")
            return 0

        partial_ownership = {t: ownership[t] for t in changed_tables if t in ownership}
        current_partial = dump_all(conn, partial_ownership, ref_run_ids, nondeterministic)
        current_partial["__schema__"] = current_schema
    finally:
        conn.close()

    partial_baseline: Dict = {"__schema__": baseline_dump.get("__schema__")}
    for t in changed_tables:
        partial_baseline[t] = baseline_dump.get(t, [])

    result = diff_dumps(partial_baseline, current_partial, ownership)
    all_unchanged = ([t for t in baseline_hashes if t not in changed_tables]
                     + result["unchanged"])
    result["unchanged"] = all_unchanged

    _print_report(result, version)
    if write_html:
        p = _write_html(result, version, host, ref_run_ids, commit)
        print(f"[audit] report: {p}")

    return 1 if result["changed"] else 0


def _print_report(result: Dict, version: str) -> None:
    changed = result["changed"]
    unchanged = result["unchanged"]

    print()
    print("=" * 60)
    print(f"CHANGED vs baseline {version}  ({len(changed)} difference(s))")
    print("=" * 60)
    if not changed:
        print("  none")
    for d in changed[:200]:
        kind = d.get("kind")
        table = d.get("table", "")
        if kind == "schema_diff":
            print(f"  SCHEMA changed")
        elif kind == "extra_table":
            print(f"  NEW TABLE: {table}")
        elif kind == "missing_table":
            print(f"  DROPPED TABLE: {table}")
        elif kind == "row_count":
            print(f"  ROW COUNT {table}: {d['old']} -> {d['new']}")
        elif kind == "missing_row":
            print(f"  MISSING ROW {table} key={d.get('key')}")
        elif kind == "cell":
            print(f"  {table}.{d.get('col')}"
                  f"  [key={d.get('key')}]"
                  f"  {d.get('old')!r} -> {d.get('new')!r}")
    if len(changed) > 200:
        print(f"  ... {len(changed) - 200} more (truncated)")

    print()
    print("=" * 60)
    print(f"UNCHANGED  ({len(unchanged)} tables identical to baseline {version})")
    print("=" * 60)
    for t in sorted(unchanged):
        print(f"  {t}")

    print()
    print(f"[audit] RESULT: {len(changed)} change(s)  "
          f"{len(unchanged)} table(s) unchanged")
