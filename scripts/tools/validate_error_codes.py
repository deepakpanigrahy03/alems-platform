#!/usr/bin/env python3
"""
Validate config/error_codes.yaml and code usage (design 39.5.2d section 10).

Checks: format and domains, required fields and enums, stage_affinity is
a C-EV stage_id, rules reference active codes, no duplicate rule predicate
sets, every code literal in core and plugins exists and is active, doc
anchors resolve, numbers never reused against the git HEAD catalog.

Usage:
  python3 scripts/tools/validate_error_codes.py
  python3 scripts/tools/validate_error_codes.py --write-docs
Exit: 0 clean, 4 violations (C-CLI invariant failure).
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.observability import error_catalog  # noqa: E402
try:  # stage vocabulary is owned by stage_graphs (master 13.1)
    from core.observability.stage_graphs import STAGE_IDS  # noqa: E402
except ImportError:  # name differs: fall back to the master 13.1 list
    STAGE_IDS = ("setup", "baseline", "measure", "persist_run", "persist_samples", "spans",
                 "attribution", "residual", "quality", "hooks", "etl_phase", "etl_hardware",
                 "integrity", "outputs")

CATALOG = ROOT / "config" / "error_codes.yaml"
DOC = ROOT / "docs-src" / "mkdocs" / "source" / "reference" / "error-codes.md"
DOMAINS = ("CFG", "ENG", "STO", "MEAS", "PERS", "SPAN", "ATTR", "ETL", "HARN",
           "PROV", "NET", "TOOL", "CLI", "OBS", "GEN")
CODE_RE = re.compile(r"^ALEMS-([A-Z]+)-(\d{4})$")
LITERAL_RE = re.compile(r"ALEMS-[A-Z]+-\d{4}")
ENUMS = {"severity": ("info", "warn", "error", "fatal"),
         "affects_validity": ("none", "partial", "invalid"),
         "status": ("active", "deprecated")}
REQUIRED = ("title", "meaning", "severity", "recoverable", "affects_validity",
            "exit_code", "status", "doc_anchor", "introduced_in")


def check_codes(codes, errs):
    """Per code format, fields, enums, affinity, deprecation."""
    for code, e in codes.items():
        m = CODE_RE.match(code)
        if not m or m.group(1) not in DOMAINS:
            errs.append("bad code or domain: %s" % code)
            continue
        if m.group(1) == "GEN" and code != "ALEMS-GEN-0000":
            errs.append("GEN allows only 0000: %s" % code)
        for f in REQUIRED:
            if f not in e:
                errs.append("%s: missing %s" % (code, f))
        for f, allowed in ENUMS.items():
            if e.get(f) not in allowed:
                errs.append("%s: %s=%r not in %s" % (code, f, e.get(f), allowed))
        aff = e.get("stage_affinity")
        if aff is not None and aff not in STAGE_IDS:
            errs.append("%s: stage_affinity %s is not a C-EV stage_id" % (code, aff))
        if e.get("status") == "deprecated" and not (e.get("replaced_by") or e.get("reason")):
            errs.append("%s: deprecated without replaced_by or reason" % code)


def check_rules(rules, codes, errs):
    """Rules point at active codes; identical predicate sets are rejected."""
    seen = {}
    for i, r in enumerate(rules):
        code = r.get("code")
        if codes.get(code, {}).get("status") != "active":
            errs.append("rule %d: code %s missing or inactive" % (i, code))
        if len(r) < 2:
            errs.append("rule %d: no predicate" % i)
        sig = error_catalog.rule_signature(r)
        if sig in seen:
            errs.append("rules %d and %d have identical predicates" % (seen[sig], i))
        seen[sig] = i


def check_literals(codes, errs):
    """Every code literal used in source exists and is active."""
    for base in ("core", "scripts", "plugins"):
        for path in (ROOT / base).rglob("*.py") if (ROOT / base).exists() else []:
            if "tests" in path.parts:
                continue
            for lit in set(LITERAL_RE.findall(path.read_text(errors="replace"))):
                if codes.get(lit, {}).get("status") != "active":
                    errs.append("%s uses unknown or inactive %s" % (path.relative_to(ROOT), lit))


def check_reuse(codes, errs):
    """Codes in the committed catalog must still exist (numbers never reused or removed)."""
    try:
        old = subprocess.run(["git", "show", "HEAD:config/error_codes.yaml"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout
    except (subprocess.CalledProcessError, OSError):
        return  # first commit of the catalog, or no git
    for code in (yaml.safe_load(old) or {}).get("codes", {}):
        if code not in codes:
            errs.append("code removed from catalog: %s (deprecate, never delete)" % code)


def check_docs(codes, errs):
    """Every active code has its anchor in the generated reference page."""
    if not DOC.exists():
        errs.append("missing %s (run --write-docs)" % DOC.relative_to(ROOT))
        return
    text = DOC.read_text()
    for code, e in codes.items():
        if e.get("status") == "active" and "{ #%s }" % e.get("doc_anchor") not in text:
            errs.append("%s: anchor %s not in reference page" % (code, e.get("doc_anchor")))


def write_docs(cat):
    """Generate the error code reference page from the catalog."""
    out = ["# Error Codes", "",
           "Generated from config/error_codes.yaml (catalog %s). Do not edit by hand." % cat["catalog_version"],
           "", "Published code meanings are permanent.", ""]
    for code, e in sorted(cat["codes"].items()):
        out += ["## %s %s { #%s }" % (code, e["title"], e["doc_anchor"]), "",
                "**Meaning:** %s" % e["meaning"], "",
                "Severity %s; recoverable %s; affects validity %s; stage affinity %s; status %s." % (
                    e["severity"], e["recoverable"], e["affects_validity"],
                    e.get("stage_affinity") or "none", e["status"]), ""]
        for label, key in (("Causes", "causes"), ("Actions", "actions")):
            if e.get(key):
                out += ["**%s:** %s" % (label, "; ".join(e[key])), ""]
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text("\n".join(out))


def main():
    """Run every check; print violations; exit 4 on any."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--write-docs", action="store_true")
    args = ap.parse_args()
    cat = error_catalog.load(CATALOG)
    if args.write_docs:
        write_docs(cat)
    errs = []
    check_codes(cat["codes"], errs)
    check_rules(cat["rules"], cat["codes"], errs)
    check_literals(cat["codes"], errs)
    check_reuse(cat["codes"], errs)
    check_docs(cat["codes"], errs)
    for e in errs:
        print("FAIL", e)
    print("%d codes, %d rules, %d violations" % (len(cat["codes"]), len(cat["rules"]), len(errs)))
    return 4 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
