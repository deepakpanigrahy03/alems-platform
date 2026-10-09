"""
BFCL task data: question entries, ground truth, A-LEMS task dicts.

Data files are the vendored upstream JSON lines files under data/
(commit recorded in UPSTREAM_COMMIT). Loading is lazy and cached; files
are read once per process and never written.
"""

import json
import os
from functools import lru_cache
from typing import Dict, List

# Upstream commit the vendored data and classes come from (S1 handover).
UPSTREAM_COMMIT = "6ea57973c7a6097fd7c5915698c54c17c5b1b6c8"
UPSTREAM_VERSION = "BFCL_v4"

# Only the multi turn categories whose classes are all vendored.
CATEGORIES = ("base", "long_context")

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def _read_jsonl(path: str) -> List[dict]:
    """Read a JSON lines file (one entry per line, blank lines skipped)."""
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


@lru_cache(maxsize=None)
def _questions(category: str) -> Dict[str, dict]:
    """Question entries of one category, keyed by id."""
    if category not in CATEGORIES:
        raise KeyError("unsupported BFCL category: %s" % category)
    path = os.path.join(_DATA_DIR, "%s_multi_turn_%s.json" % (UPSTREAM_VERSION, category))
    return {e["id"]: e for e in _read_jsonl(path)}


@lru_cache(maxsize=None)
def _answers(category: str) -> Dict[str, list]:
    """Ground truth (list of turns of call strings) keyed by id."""
    path = os.path.join(_DATA_DIR, "possible_answer",
                        "%s_multi_turn_%s.json" % (UPSTREAM_VERSION, category))
    return {e["id"]: e["ground_truth"] for e in _read_jsonl(path)}


def category_of(task_id: str) -> str:
    """'multi_turn_long_context_12' -> 'long_context' (upstream id scheme)."""
    return task_id.rsplit("_", 1)[0].replace("multi_turn_", "", 1)


def list_ids(category: str = "base") -> List[str]:
    """All entry ids of a category, in file order."""
    return list(_questions(category))


def load_entry(task_id: str) -> dict:
    """The question entry for one id; KeyError when unknown."""
    return _questions(category_of(task_id))[task_id]


def ground_truth(task_id: str) -> List[List[str]]:
    """Ground truth call strings per turn for one id."""
    return _answers(category_of(task_id))[task_id]


def load_func_docs(class_name: str) -> List[dict]:
    """Upstream function docs (one dict per function) for one class."""
    from .env import CLASS_MODULES
    path = os.path.join(_DATA_DIR, "multi_turn_func_doc",
                        CLASS_MODULES[class_name] + ".json")
    return _read_jsonl(path)


def to_alems_task(task_id: str) -> dict:
    """
    A-LEMS task dict for one BFCL entry.

    The agent adapter (S1b) reads turns and involved_classes; the scorer
    reads scorer and expected. Nothing here is persisted by this package.
    """
    entry = load_entry(task_id)
    turns = [[m["content"] for m in turn if m.get("role") == "user"]
             for turn in entry["question"]]
    return {
        "task_id": "bfcl_" + task_id,
        "bfcl_id": task_id,
        "category": category_of(task_id),
        "turns": turns,
        "involved_classes": list(entry["involved_classes"]),
        "excluded_function": list(entry.get("excluded_function", [])),
        "scorer": "bfcl_state",
        "expected": task_id,
        "upstream_commit": UPSTREAM_COMMIT,
    }
