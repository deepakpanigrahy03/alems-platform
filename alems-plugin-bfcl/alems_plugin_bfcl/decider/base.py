"""
RAF R6 Decider interface (SPEC_RAF_R6 addendum A, coordinator addendum D).

A decider chooses the recovery option before attempt n >= 2 of a goal. It
runs in the pre attempt hook, which is before t0, so nothing here is inside
a measurement window (C-BND rule 4). This module holds only types and pure
helpers; it imports the standard library only (INV-14).

Inputs are restricted to what is known at failure (addendum D3): failure
class, work done so far (calls, tokens, joules when reachable), attempt
number and context. Task ids, early or late labels, occupancy_fraction and
the checker output of the current goal are never part of FailureState.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:                                   # Protocol is in typing from 3.8
    from typing import Protocol
except ImportError:                    # pragma: no cover, Python 3.7 only
    Protocol = object                  # type: ignore

INTERFACE_VERSION = "1.0.0"

# Option space (addendum D2): depth x cache, plus abandon. Names match
# prepare.POLICIES so a decision maps to the existing restore and reset
# paths without translation.
DEPTHS = ("FR", "SR", "RB", "SG")
CACHES = ("warm", "cold")
ABANDON = "ABANDON"
OPTIONS: Tuple[str, ...] = tuple(
    "%s_%s" % (d, c) for d in DEPTHS for c in CACHES) + (ABANDON,)

# Escalation ladder (R5 6.3): resume, roll back, restart, abandon. SG is a
# resume variant, so it shares the first rung with SR.
LADDER: Tuple[Tuple[str, ...], ...] = (("SR", "SG"), ("RB",), ("FR",), (ABANDON,))

# Fields a FailureState builder must never copy (addendum D3, D5, rule 2).
FORBIDDEN_KEYS = frozenset({
    "task", "task_id", "goal_key", "early", "late", "position_label",
    "occupancy_fraction", "pass_fail", "checker", "ground_truth"})

# Source of the work done measure, recorded so the ledger shows the fallback.
WORK_SOURCE_JOULES = "joules"
WORK_SOURCE_CALLS = "calls"


@dataclass(frozen=True)
class FailureState:
    """Everything a decider may see at the decision point."""
    failure_class: str                  # session.failure kind as recorded
    attempt: int                        # attempt about to start (>= 2)
    calls_done: int                     # LLM calls completed before failure
    tokens_context: Optional[int]       # prompt tokens of the last call
    tokens_cached: Optional[int]        # cached tokens of the last call
    joules_done: Optional[float]        # None when not reachable in memory
    work_source: str                    # WORK_SOURCE_JOULES or _CALLS
    turns_done: int                     # completed turns before failure
    turn_starts: Tuple[int, ...]        # tool calls made before each turn
    previous_options: Tuple[str, ...]   # options already tried, attempt order
    error_text: str = ""                # failure message, for laya_p only
    last_turns: Tuple[str, ...] = ()    # last user turn, last agent messages

    def __post_init__(self) -> None:
        # A decision is only taken from attempt 2 on (PreAttempt contract).
        if self.attempt < 2:
            raise ValueError("decisions start at attempt 2")
        if self.work_source not in (WORK_SOURCE_JOULES, WORK_SOURCE_CALLS):
            raise ValueError("unknown work_source %r" % self.work_source)


@dataclass(frozen=True)
class OptionScore:
    """Predicted cost and success for one option."""
    option: str
    p: Optional[float]                  # success probability, None if abandon
    j_pred: Optional[float]             # predicted J per success, joules
    e_pred: Optional[float] = None      # predicted energy of the attempt


@dataclass(frozen=True)
class Decision:
    """Result of decide(); detail goes to the PreAttempt record (G1 A)."""
    option: str
    decider: str
    params_version: str
    scores: Tuple[OptionScore, ...]
    fallback_reason: Optional[str] = None   # set when the fail closed path ran
    x_star_calls: Optional[float] = None    # threshold applied, work done units

    def __post_init__(self) -> None:
        if self.option not in OPTIONS:
            raise ValueError("unknown option %r" % self.option)

    def detail(self) -> Dict[str, Any]:
        """Plain dict merged into _raf_decisions[attempt] for the ledger."""
        d = asdict(self)
        d["scores"] = [asdict(s) for s in self.scores]
        d["interface_version"] = INTERFACE_VERSION
        return d


@dataclass(frozen=True)
class Outcome:
    """Checker result of a finished goal, passed to update() at goal end."""
    failure_class: str
    options: Tuple[str, ...]            # option per attempt from 2 on
    passed: bool                        # output_quality pass_fail of the goal
    abandoned: bool
    attempts: int
    calls: Tuple[Dict[str, Any], ...] = field(default_factory=tuple)


class Decider(Protocol):
    """Pluggable decider (addendum A section 1)."""
    name: str

    def decide(self, state: FailureState, options: List[str]) -> Decision:
        """Choose one of options; must not read store, network except laya_p."""
        ...

    def update(self, outcome: Outcome) -> None:
        """Outer loop learning after a goal (R5 6.6); may be a no op."""
        ...


def check_state_dict(raw: Dict[str, Any]) -> None:
    """Reject forbidden keys before a FailureState is built (D3, D5)."""
    bad = FORBIDDEN_KEYS.intersection(raw)
    if bad:
        raise ValueError("forbidden decision inputs: %s" % sorted(bad))


def ladder_rung(option: str) -> int:
    """Index of the rung an option sits on; abandon is the last rung."""
    depth = option if option == ABANDON else option[:2]
    for i, rung in enumerate(LADDER):
        if depth in rung:
            return i
    raise ValueError("option not on the ladder: %r" % option)
