#!/usr/bin/env python3
"""
Methodology documentation loader.
Reads methodology_docs.yaml and returns doc/method_anchor/section per method_id.

Used by seed_methodology.py to avoid hardcoding doc references.
"""

import yaml
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
METHODOLOGY_DOCS = REPO_ROOT / "config" / "methodology_docs.yaml"


def load_methodology_docs() -> dict:
    """
    Load methodology_docs.yaml and return a flat dict:
        method_id -> {doc, method_anchor, section}

    Returns empty dict for any method_id not in the map.
    Callers should warn if a method_id is missing.
    """
    with open(METHODOLOGY_DOCS) as f:
        data = yaml.safe_load(f)

    methods = data.get("methods", {})
    docs_base = data.get("docs_base", "")

    result = {}
    for method_id, entry in methods.items():
        result[method_id] = {
            "doc":           entry.get("doc", ""),
            "method_anchor": entry.get("method_anchor", ""),
            "section":       entry.get("section", ""),
            "docs_base":     docs_base,
        }

    return result


def get_method_doc_ref(method_id: str) -> dict:
    """
    Convenience function: return doc reference for one method_id.
    Returns empty dict if not found.
    """
    docs = load_methodology_docs()
    return docs.get(method_id, {})
