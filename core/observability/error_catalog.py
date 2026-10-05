"""
Error catalog loading and classification (C-ERR v2, design sections 3, 5).

Classification tiers, first tier that yields a code decides:
  1. AlemsError raised with a code
  2. site declared code
  3. catalog rules, highest ranked match (total order, section 5.1)
  4. ALEMS-GEN-0000

Rules match class names as strings, so optional libraries (openai,
anthropic, httpx) are never imported here; a library absent on the host
simply never matches.
"""
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from core.errors import GENERIC_CODE

# Engine root is two levels above core/observability/.
_CATALOG_PATH = Path(__file__).resolve().parents[2] / "config" / "error_codes.yaml"
# Predicate precedence for ranking ties on predicate count (section 5.1 rule 2).
_PRECEDENCE = ("http_status", "class", "origin", "component", "message")
_lock = threading.Lock()
_cache = {}  # type: Dict[str, Any]


def load(path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Load and cache the catalog.

    Returns:
        dict with catalog_version, codes, rules (empty structures when the
        file is missing, so capture still works and codes fall back to GEN).
    """
    key = str(path or _CATALOG_PATH)
    with _lock:
        if key in _cache:
            return _cache[key]
        p = Path(key)
        data = yaml.safe_load(p.read_text()) if p.exists() else {}
        data = data or {}
        data.setdefault("catalog_version", None)
        data.setdefault("codes", {})
        data.setdefault("rules", [])
        _cache[key] = data
        return data


def reset_cache() -> None:
    """Drop cached catalogs (tests, catalog edits in a live process)."""
    with _lock:
        _cache.clear()


def lookup(code: str) -> Optional[Dict[str, Any]]:
    """Catalog entry for a code, or None if unknown."""
    return load()["codes"].get(code)


def http_status(exc: BaseException) -> Optional[int]:
    """HTTP status carried by an exception from common client libraries."""
    for value in (getattr(exc, "status_code", None),
                  getattr(getattr(exc, "response", None), "status_code", None),
                  getattr(exc, "status", None)):
        if isinstance(value, int):
            return value
    return None


def origin_module(exc: BaseException) -> Optional[str]:
    """
    Module of the innermost traceback frame (where the failure was raised).

    Called only outside the measurement window: it walks the traceback,
    which master 5.2a forbids inside [t0, t1].
    """
    tb = exc.__traceback__
    if tb is None:
        return None
    while tb.tb_next is not None:
        tb = tb.tb_next
    return tb.tb_frame.f_globals.get("__name__")


def _class_distance(exc: BaseException, name: str) -> Optional[int]:
    """
    MRO distance from the exception class to a rule class name, or None.

    "pkg.Name" matches a class with qualname Name defined in pkg or any
    submodule (openai.AuthenticationError lives in openai._exceptions).
    A bare name matches builtins only.
    """
    pkg, _, short = name.rpartition(".")
    for distance, cls in enumerate(type(exc).__mro__):
        if cls.__qualname__ != short:
            continue
        mod = cls.__module__
        if not pkg and mod == "builtins":
            return distance
        if pkg and (mod == pkg or mod.startswith(pkg + ".")):
            return distance
    return None


def _match(rule: Dict[str, Any], facts: Dict[str, Any]) -> Optional[Tuple]:
    """
    Rank key for a matching rule, or None if any predicate fails.

    Key (higher wins): predicate count, then a precedence bit vector, then
    within predicate specificity (exact status over range, nearer class,
    longer prefixes).
    """
    spec = []  # within predicate specificity, in precedence order
    status = facts["status"]
    if "http_status" in rule or "http_status_range" in rule:
        if "http_status" in rule:
            if status != rule["http_status"]:
                return None
            spec.append(2)  # exact status beats a range
        else:
            lo, hi = rule["http_status_range"]
            if status is None or not lo <= status <= hi:
                return None
            spec.append(1)
    if "class" in rule:
        d = _class_distance(facts["exc"], rule["class"])
        if d is None:
            return None
        spec.append(-d)  # nearer in the MRO is more specific
    for pred, value in (("origin", facts["origin"]), ("component", facts["component"])):
        if pred not in rule:
            continue
        if not value or not (value == rule[pred] or value.startswith(rule[pred] + ".")):
            return None
        spec.append(len(rule[pred]))
    if "message" in rule:
        if not re.search(rule["message"], facts["message"]):
            return None
        spec.append(0)
    preds = [p for p in _PRECEDENCE if p in rule or (p == "http_status" and "http_status_range" in rule)]
    bits = tuple(1 if p in preds else 0 for p in _PRECEDENCE)
    return (len(preds), bits, tuple(spec))


def classify(exc: BaseException, site_code: Optional[str] = None,
             component: Optional[str] = None) -> str:
    """
    Resolve the catalog code for an exception (tiers in the module doc).

    Never raises: any internal failure yields GENERIC_CODE.
    """
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code.startswith("ALEMS-"):
        return code  # tier 1: AlemsError
    if site_code:
        return site_code  # tier 2
    try:
        facts = {"exc": exc, "status": http_status(exc), "origin": origin_module(exc),
                 "component": component, "message": str(exc)}
        best = None  # type: Optional[Tuple[Tuple, str]]
        for rule in load()["rules"]:
            key = _match(rule, facts)
            if key is not None and (best is None or key > best[0]):
                best = (key, rule["code"])
        return best[1] if best else GENERIC_CODE
    except Exception:  # noqa: BLE001  classification must never break capture
        return GENERIC_CODE


def rule_signature(rule: Dict[str, Any]) -> Tuple:
    """Predicate set of a rule, used by the validator to reject duplicates."""
    return tuple(sorted((k, str(v)) for k, v in rule.items() if k != "code"))


def codes_in(rules: List[Dict[str, Any]]) -> List[str]:
    """Codes referenced by rules (validator helper)."""
    return [r.get("code") for r in rules]
