"""
RAF hooks of the BFCL plugin, loaded by core's scheduled runner through
entry points (INV-2); core never imports this module directly.

    alems.harness.prepare   bfcl           make_prepare
    alems.harness.deciders  escalate_stub  escalate_stub (R6 Decider replaces it)
    alems.harness.validity  bfcl_raf       classify
"""

from typing import Any, Callable, Dict, Optional

from . import raf_validity
from .prepare import PreAttempt

ESCALATE_POLICY = "FR_cold"


def make_prepare(executor: Any, base_url: str, policy: str,
                 reset_path: Optional[str] = None,
                 decide: Optional[Callable[[int, Any], Optional[str]]] = None,
                 metrics: Optional[Callable[[str], Any]] = None) -> PreAttempt:
    """Pre attempt hook for one goal execution (cache reset, restore, decision, cache statistics)."""
    return PreAttempt(executor, base_url, policy, reset_path=reset_path, decide=decide,
                      metrics=metrics)


def escalate_stub(attempt: int, executor: Any) -> Optional[str]:
    """Persistent escalating arm (R2 8.1): keep the policy at attempt 2, escalate at 3."""
    return ESCALATE_POLICY if attempt >= 3 else None


def classify(conn: Any, goal_id: int, plan: Dict[str, Any],
             prepare: Optional[Dict[int, Dict[str, Any]]] = None,
             attempt_meta: Optional[Dict[int, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """RAF validity codes for one goal execution (pilot rules plus R1, R2 codes)."""
    return raf_validity.classify(conn, goal_id, plan, prepare, attempt_meta)
