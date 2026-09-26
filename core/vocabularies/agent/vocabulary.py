# core/vocabularies/agent/vocabulary.py
# Declares the agent span kinds and their valid parent relationships.
# No I/O. No DB. Pure data.

from typing import Optional, Set, Dict

# Canonical kind strings for the agent vocabulary.
# Used for CHECK constraints in span inserts and for vocabulary validation.
AGENT_KINDS: Set[str] = {
    "experiment",   # top-level span for an entire experiment (exp_id)
    "run",          # one run_id; child of experiment or standalone
    "goal",         # one goal_id; child of run (agentic only)
    "attempt",      # one attempt_id; child of goal
    "turn",         # one dialogue turn; child of attempt
    "llm_call",     # one LLM API call; child of turn or attempt
    "tool_call",    # one tool invocation; child of turn or attempt
    "phase",        # planning|execution|synthesis window; child of attempt
    "retry",        # retry boundary event; child of goal
    "recovery",     # recovery action; child of goal
}

# Valid parent kinds per child kind.
# None means the kind may be a root (parent_span_id is NULL).
VALID_PARENTS: Dict[str, Optional[Set[str]]] = {
    "experiment": None,
    "run":        {"experiment", None},   # type: ignore[arg-type]
    "goal":       {"run"},
    "attempt":    {"goal"},
    "turn":       {"attempt"},
    "llm_call":   {"turn", "attempt"},
    "tool_call":  {"turn", "attempt"},
    "phase":      {"attempt"},
    "retry":      {"goal"},
    "recovery":   {"goal"},
}


class AgentVocabulary:
    """
    Vocabulary descriptor for the agent span family.

    Provides kind validation and parent relationship checks.
    Does not touch the database directly.
    """

    VOCABULARY_ID = "agent"
    VOCABULARY_VERSION = "1"

    # Placement: which kinds get placement rows at emit time.
    # llm_call and phase spans get placements from the known device context.
    PLACED_KINDS: Set[str] = {"llm_call", "phase"}

    @classmethod
    def is_valid_kind(cls, kind: str) -> bool:
        """Return True if kind is a recognized agent span kind."""
        return kind in AGENT_KINDS

    @classmethod
    def is_valid_parent(cls, child_kind: str, parent_kind: Optional[str]) -> bool:
        """
        Return True if parent_kind is a valid parent for child_kind.

        Args:
            child_kind:  The kind of the span being opened.
            parent_kind: The kind of the parent span, or None for a root span.

        Returns:
            True when the relationship is declared in VALID_PARENTS.
        """
        if child_kind not in VALID_PARENTS:
            # Unknown kind; reject.
            return False
        allowed = VALID_PARENTS[child_kind]
        if allowed is None:
            # Kind may appear as root; any parent is also accepted.
            return True
        # allowed is a set that may include the sentinel None for root-allowed kinds.
        return parent_kind in allowed

    @classmethod
    def needs_placement(cls, kind: str) -> bool:
        """Return True if spans of this kind should get placement rows."""
        return kind in cls.PLACED_KINDS
