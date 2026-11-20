"""
Span vocabulary contract (entry point group alems.spans.vocabularies).

A vocabulary declares which span kinds exist, which parent each kind may
have, which kinds get a placement row, and the typed attributes of each
kind. Writers call validate_open() when a span opens and validate_close()
when it closes, so a wrong kind, parent, type or a missing required
attribute is refused before anything reaches the store.

The contract is a descriptor object, not a base class: a plugin exposes a
SpanVocabulary instance. Older vocabularies that only expose
is_valid_kind / is_valid_parent / needs_placement keep working; this
class offers the same three methods, so callers can treat both alike.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Optional, Tuple

# value types allowed by the span_attributes.value_type CHECK constraint
VALUE_TYPES = ("string", "int", "float", "bool", "json")


class SpanContractError(ValueError):
    """A span broke its vocabulary (unknown kind, wrong parent, bad attribute)."""


@dataclass(frozen=True)
class AttrSpec:
    """
    One attribute of a span kind.

    Args:
        type: one of VALUE_TYPES.
        required: the span may not close without it.
        allowed: if given, the only values accepted.
    """
    type: str
    required: bool = False
    allowed: Optional[Tuple[Any, ...]] = None

    def __post_init__(self) -> None:
        """Refuse a type the span_attributes table cannot store."""
        if self.type not in VALUE_TYPES:
            raise SpanContractError("unknown attribute type %r" % self.type)


@dataclass(frozen=True)
class KindSpec:
    """
    One span kind.

    Args:
        parents: allowed parent kinds; None in the set means "may be a root".
        attributes: attribute name -> AttrSpec.
        placed: spans of this kind get a span_placements row.
    """
    parents: FrozenSet[Optional[str]]
    attributes: Dict[str, AttrSpec] = field(default_factory=dict)
    placed: bool = True


@dataclass(frozen=True)
class SpanVocabulary:
    """A named, versioned set of span kinds."""
    vocabulary_id: str
    version: str
    kinds: Dict[str, KindSpec]

    # same three methods as the older class-style vocabularies

    def is_valid_kind(self, kind: str) -> bool:
        """True if kind belongs to this vocabulary."""
        return kind in self.kinds

    def is_valid_parent(self, child_kind: str, parent_kind: Optional[str]) -> bool:
        """True if parent_kind (None for a root span) may hold child_kind."""
        spec = self.kinds.get(child_kind)
        return spec is not None and parent_kind in spec.parents

    def needs_placement(self, kind: str) -> bool:
        """True if spans of this kind get a placement row."""
        spec = self.kinds.get(kind)
        return bool(spec and spec.placed)

    def validate_open(self, kind: str, parent_kind: Optional[str]) -> None:
        """
        Check a span before it opens.

        Raises:
            SpanContractError: unknown kind or a parent the kind may not have.
        """
        if not self.is_valid_kind(kind):
            raise SpanContractError("%s: unknown kind %r" % (self.vocabulary_id, kind))
        if not self.is_valid_parent(kind, parent_kind):
            raise SpanContractError("%s: %r may not have parent %r"
                                    % (self.vocabulary_id, kind, parent_kind))

    def validate_close(self, kind: str, attributes: Dict[str, Any]) -> None:
        """
        Check the attributes of a span when it closes.

        None counts as absent (MIC-1: missing is NULL, never a made-up value).

        Raises:
            SpanContractError: unknown attribute, wrong type, value not
            allowed, or a required attribute missing.
        """
        spec = self.kinds.get(kind)
        if spec is None:
            raise SpanContractError("%s: unknown kind %r" % (self.vocabulary_id, kind))
        for key, value in attributes.items():
            _check_value(self.vocabulary_id, kind, key, value, spec.attributes.get(key))
        missing = [k for k, a in spec.attributes.items()
                   if a.required and attributes.get(k) is None]
        if missing:
            raise SpanContractError("%s: %r closed without required %s"
                                    % (self.vocabulary_id, kind, ", ".join(sorted(missing))))


def _check_value(vocab: str, kind: str, key: str, value: Any,
                 spec: Optional[AttrSpec]) -> None:
    """Refuse an undeclared key, a wrong type or a value outside 'allowed'."""
    if spec is None:
        raise SpanContractError("%s: %r has no attribute %r" % (vocab, kind, key))
    if value is None:
        return                      # absent; required-ness is checked by the caller
    if not _type_ok(spec.type, value):
        raise SpanContractError("%s: %r.%s must be %s, got %r"
                                % (vocab, kind, key, spec.type, value))
    if spec.allowed is not None and value not in spec.allowed:
        raise SpanContractError("%s: %r.%s must be one of %s, got %r"
                                % (vocab, kind, key, list(spec.allowed), value))


def _type_ok(type_name: str, value: Any) -> bool:
    """Strict type test; bool is not accepted as int or float (True == 1 trap)."""
    if type_name == "bool":
        return isinstance(value, bool)
    if isinstance(value, bool):
        return False
    if type_name == "int":
        return isinstance(value, int)
    if type_name == "float":
        return isinstance(value, (int, float))
    if type_name == "string":
        return isinstance(value, str)
    return isinstance(value, (dict, list))          # json


def encode_attribute(spec: AttrSpec, value: Any) -> Tuple[str, str]:
    """
    Text form and value_type of an attribute for the span_attributes table.

    Returns:
        (value_text, value_type). Booleans are stored as 'true' / 'false'
        (as the agent span writer does).
    """
    # same text forms as the agent span writer, so one reader parses both
    if spec.type == "bool":
        return ("true" if value else "false"), "bool"
    if spec.type == "json":
        return json.dumps(value), "json"
    if spec.type == "float":
        return str(float(value)), "float"
    return str(value), spec.type
