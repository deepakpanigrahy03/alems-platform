#!/usr/bin/env python3
"""
A-LEMS Methodology Reference Validator
---------------------------------------
Validates that every method entry in methodology_docs.yaml resolves to:
  1. An existing documentation file
  2. A section heading matching the section field (case-insensitive)
  3. A unique method_anchor across the docs map
  4. A matching entry in seed_methodology.py

The validator does NOT require explicit { #anchor } tags in docs.
Existing section headings are matched by text against the section field.
method_anchor is a DB identifier only — it lives in methodology_docs.yaml
and the database, not in the markdown files.

Usage:
    python3 scripts/tools/validate_methodology_refs.py
    python3 scripts/tools/validate_methodology_refs.py --strict
    python3 scripts/tools/validate_methodology_refs.py --check-db
"""

import argparse
import re
import sqlite3
import sys
import yaml
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
METHODOLOGY_DOCS = REPO_ROOT / "config" / "methodology_docs.yaml"
SEED_METHODOLOGY = REPO_ROOT / "scripts" / "seed_methodology.py"


def load_methodology_docs() -> dict:
    with open(METHODOLOGY_DOCS) as f:
        return yaml.safe_load(f)


def check_doc_exists(docs_base: Path, method_id: str, entry: dict, results: list) -> bool:
    doc = entry.get("doc", "")
    doc_path = docs_base / doc
    if not doc_path.exists():
        results.append(("FAIL", method_id, f"doc not found: {doc}"))
        return False
    return True


def check_section_in_doc(docs_base: Path, method_id: str, entry: dict, results: list) -> bool:
    """
    Check that the section text appears as a heading in the doc.
    Matches ## heading text (case-insensitive, strips emoji and extra whitespace).
    Does not require explicit { #anchor } tags.
    """
    doc = entry.get("doc", "")
    section = entry.get("section", "").strip()
    anchor = entry.get("method_anchor", "")
    doc_path = docs_base / doc

    if not section:
        results.append(("FAIL", method_id, "section field missing"))
        return False

    # Normalize: strip emoji, extra spaces, lowercase
    def normalize(text):
        text = re.sub(r'[^\x00-\x7F]', '', text)  # remove non-ASCII (emoji)
        text = re.sub(r'\s+', ' ', text).strip().lower()
        return text

    section_norm = normalize(section)
    content = doc_path.read_text(errors="replace")

    for line in content.splitlines():
        if not line.startswith("#"):
            continue
        # Strip leading # chars and spaces
        heading_text = re.sub(r'^#+\s*', '', line)
        # Strip any existing { #anchor } tags
        heading_text = re.sub(r'\{[^}]*\}', '', heading_text).strip()
        if normalize(heading_text) == section_norm:
            results.append(("OK", method_id, f"{doc} → '{section}' [{anchor}]"))
            return True

    results.append((
        "FAIL",
        method_id,
        f"section '{section}' not found as heading in {doc}"
    ))
    return False


def check_anchor_uniqueness(methods: dict, results: list) -> None:
    """Check that no two methods share the same method_anchor."""
    seen = {}
    for method_id, entry in methods.items():
        anchor = entry.get("method_anchor", "")
        if not anchor:
            continue
        if anchor in seen:
            results.append((
                "FAIL",
                method_id,
                f"method_anchor '{anchor}' already used by {seen[anchor]}"
            ))
        else:
            seen[anchor] = method_id


def check_seed_coverage(methods: dict, results: list) -> None:
    """Warn if a method_id in the docs map is not in seed_methodology.py."""
    seed_text = SEED_METHODOLOGY.read_text()
    for method_id in methods:
        if f'"{method_id}"' not in seed_text and f"'{method_id}'" not in seed_text:
            results.append((
                "WARN",
                method_id,
                "method_id not found in seed_methodology.py — orphaned entry"
            ))


def check_db_consistency(docs: dict, results: list) -> None:
    """Optional: verify method_anchor values in live DB resolve to current docs."""
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from scripts.tools.path_loader import get_alems_db_path
        db_path = get_alems_db_path()
    except Exception as exc:
        results.append(("WARN", "db", f"Cannot resolve DB path: {exc}"))
        return

    if not Path(db_path).exists():
        results.append(("WARN", "db", f"DB not found at {db_path} — skipping"))
        return

    docs_base = REPO_ROOT / docs["docs_base"]
    methods = docs.get("methods", {})
    anchor_to_method = {
        entry["method_anchor"]: mid
        for mid, entry in methods.items()
        if "method_anchor" in entry
    }

    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT method_anchor FROM measurement_methodology "
            "WHERE method_anchor IS NOT NULL"
        )
        db_anchors = {row[0] for row in cur.fetchall()}
        conn.close()
    except Exception as exc:
        results.append(("WARN", "db", f"DB query failed: {exc}"))
        return

    for anchor in db_anchors:
        if anchor not in anchor_to_method:
            results.append((
                "WARN",
                f"db:{anchor}",
                f"method_anchor '{anchor}' in DB has no entry in methodology_docs.yaml"
            ))


def print_results(results: list) -> tuple:
    fails = [r for r in results if r[0] == "FAIL"]
    warns = [r for r in results if r[0] == "WARN"]
    oks   = [r for r in results if r[0] == "OK"]

    col = max((len(r[1]) for r in results), default=20) + 2
    for status, subject, message in results:
        marker = {"OK": "  OK  ", "FAIL": " FAIL ", "WARN": " WARN "}[status]
        print(f"{marker} {subject:<{col}} {message}")

    print()
    print(f"  {len(oks)} passed  {len(warns)} warnings  {len(fails)} failures")
    return len(fails), len(warns)


def main():
    parser = argparse.ArgumentParser(
        description="Validate A-LEMS methodology documentation references"
    )
    parser.add_argument("--strict", action="store_true",
                        help="Treat warnings as failures")
    parser.add_argument("--check-db", action="store_true",
                        help="Also validate method_anchor values in live DB")
    args = parser.parse_args()

    docs = load_methodology_docs()
    docs_base = REPO_ROOT / docs["docs_base"]
    methods = docs.get("methods", {})

    print(f"Validating {len(methods)} methodology references...")
    print(f"Docs base: {docs_base}")
    print()

    results = []

    # Anchor uniqueness across all methods
    check_anchor_uniqueness(methods, results)

    # Per-method checks
    for method_id, entry in methods.items():
        doc_ok = check_doc_exists(docs_base, method_id, entry, results)
        if doc_ok:
            check_section_in_doc(docs_base, method_id, entry, results)

    # Seed coverage
    check_seed_coverage(methods, results)

    # Optional DB check
    if args.check_db:
        print("Checking DB consistency...")
        check_db_consistency(docs, results)

    fail_count, warn_count = print_results(results)

    if fail_count > 0:
        print("\nFix failures before building docs.")
        sys.exit(1)

    if args.strict and warn_count > 0:
        print("\nStrict mode: warnings treated as failures.")
        sys.exit(1)

    print("\nAll methodology references valid.")
    sys.exit(0)


if __name__ == "__main__":
    main()
