"""
Live smoke for bfcl_react against a running model server (no harness).

Runs one BFCL task as B0, then with one injected failure under FR and SR,
and prints one line per attempt: status, calls, cached tokens, restore
hash check, and the bfcl_state score. No measurement, no store writes.

Usage (GN100):
    python -m alems_plugin_bfcl.smoke --base-url http://localhost:8000/v1 \
        --model Mistral-7B-Instruct-v0.3 --task multi_turn_base_0 \
        --cls tool_error --phase tool --ordinal 2
"""

import argparse
import json
import time
from types import SimpleNamespace

from .react import BFCLReactAdapter
from .scorer import BFCLStateScorer


class _OnceInjector:
    """Duck typed stand in for the platform injector: fires once at k."""

    def __init__(self, cls: str, phase: str, ordinal: int) -> None:
        self.cls, self.phase, self.ordinal, self.fired = cls, phase, ordinal, 0

    def is_active(self) -> bool:
        return True

    def should_inject(self, step_index, phase, tool_name, draw_number, attempt_id, goal_id):
        if not self.fired and phase == self.phase and step_index == self.ordinal:
            self.fired = 1
            return True, self.cls
        return False, None


def _line(label: str, meta: dict, task: str, seconds: float) -> str:
    raf = meta["raf"]
    rec = raf.get("recovery") or {}
    cached = [c["cached_tokens"] for c in raf["llm_calls"]]
    score = BFCLStateScorer().score(raf["scorer_output"], task)
    hash_ok = (rec.get("state_hash_after_restore") == rec.get("expected_hash")) if rec else "n/a"
    return ("%-9s %-8s err=%-15s llm=%-3d tool=%-3d tokens=%-6d cached=%s fired=%s "
            "hash_ok=%s score=%.0f (%s) %.1fs"
            % (label, meta["execution"]["status"], meta["execution"]["error_type"],
               meta["llm_calls"], meta["tool_count"], meta["tokens"]["total"],
               cached, raf["injection"]["fired"], hash_ok, score[0], score[2][:40], seconds))


def _attempt(adapter, executor, task: str):
    cfg = {"model_config": executor.config, "task": "", "tool_graph": None,
           "executor": executor, "task_id": "bfcl_" + task}
    t0 = time.time()
    meta = adapter.execute_task(cfg, tools=[], engine=None).metadata
    return meta, time.time() - t0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--base-url", default="http://localhost:8000/v1")
    ap.add_argument("--model", default="Mistral-7B-Instruct-v0.3")
    ap.add_argument("--task", default="multi_turn_base_0")
    ap.add_argument("--cls", default="tool_error",
                    choices=["tool_error", "timeout", "rate_limit", "json_parse"])
    ap.add_argument("--phase", default="tool", choices=["tool", "llm"])
    ap.add_argument("--ordinal", type=int, default=2)
    ap.add_argument("--dump", help="write all attempt metadata to this JSON file")
    a = ap.parse_args()
    adapter = BFCLReactAdapter()
    config = {"model_id": a.model, "base_url": a.base_url, "max_tokens": 1024}
    dump = {}
    ex = SimpleNamespace(config=config, failure_injector=None, _current_attempt=1,
                         _current_attempt_id=1, _raf_context={"goal_key": "b0"})
    meta, s = _attempt(adapter, ex, a.task)
    print(_line("B0", meta, a.task, s))
    dump["B0"] = meta
    for policy in ("FR", "SR"):
        ex = SimpleNamespace(config=config,
                             failure_injector=_OnceInjector(a.cls, a.phase, a.ordinal),
                             _current_attempt=1, _current_attempt_id=1,
                             _raf_context={"goal_key": policy, "policy": policy})
        meta, s = _attempt(adapter, ex, a.task)
        print(_line(policy + " a1", meta, a.task, s))
        dump[policy + "_a1"] = meta
        if meta["execution"]["status"] != "failure":
            print("%s: injection did not end attempt 1 (ordinal not reached?)" % policy)
            continue
        ex._current_attempt, ex._current_attempt_id = 2, 2
        meta, s = _attempt(adapter, ex, a.task)
        print(_line(policy + " a2", meta, a.task, s))
        dump[policy + "_a2"] = meta
    if a.dump:
        with open(a.dump, "w", encoding="utf-8") as fh:
            json.dump(dump, fh, indent=1, default=str)


if __name__ == "__main__":
    main()
