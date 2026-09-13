#!/usr/bin/env python3
"""
================================================================================
ADAPTER REGISTRY  —  core/readers/registry.py
================================================================================

Purpose:
    Generic typed registry for A-LEMS reader adapter families.
    One instance per reader family, created in bootstrap.py.

    Contract (architectural decisions 2026-09-13):
        INV-5: Deterministic dispatch — PRIORITY among eligible only.
               Ties raise ConfigurationError immediately.
        INV-6: No silent replacement — duplicate METHOD_ID raises
               DuplicateRegistrationError at registration time.

    Dummies are NOT registered here.
    NoAdapterError propagates to factory.py, which decides:
        research mode → fail loud, stop the experiment
        limited mode  → return dummy, tag run as LIMITED in DB

Author: Deepak Panigrahy
Spec:   SPEC 35A, Phase 1
================================================================================
"""

import logging
from typing import Generic, List, Type, TypeVar

logger = logging.getLogger(__name__)

# T is the family ABC type (EnergyReaderABC, ThermalReaderABC, etc.)
T = TypeVar("T")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class DuplicateRegistrationError(Exception):
    """
    Two reader classes claim the same METHOD_ID.
    INV-6: never silently overwrite. Raised at registration time.
    """


class ConfigurationError(Exception):
    """
    Two eligible readers tie on PRIORITY.
    INV-5: ties are configuration errors, not silent choices.
    """


class NoAdapterError(Exception):
    """
    No registered real reader passes can_handle() for the given caps.
    Dummies are not in the registry.
    factory.py catches this and decides: fail loud vs LIMITED mode.
    """


# ---------------------------------------------------------------------------
# AdapterRegistry
# ---------------------------------------------------------------------------

class AdapterRegistry(Generic[T]):
    """
    Registry for one reader adapter family.

    Generic over T so callers get typed returns:
        energy_registry: AdapterRegistry[EnergyReaderABC]
        energy_registry.select(caps) -> Type[EnergyReaderABC]

    Usage:
        registry = AdapterRegistry(family="energy")
        registry.register(RAPLReader)
        winner_cls = registry.select(caps)
        reader = winner_cls(config)
    """

    def __init__(self, family: str) -> None:
        self._family: str = family
        # METHOD_ID -> reader class. Populated by register(), read by select().
        self._classes: dict = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, cls: Type[T]) -> None:
        """
        Add a reader class to this family registry.

        Class must expose:
            METHOD_ID: str              unique identity for this family
            PRIORITY:  int              dispatch order, lower wins
            can_handle(caps) -> bool    classmethod, eligibility check

        Raises:
            DuplicateRegistrationError  if METHOD_ID already registered (INV-6)
            AttributeError              if METHOD_ID missing (programming error)
        """
        key = cls.METHOD_ID  # AttributeError here is intentional

        if key in self._classes:
            existing = self._classes[key]
            raise DuplicateRegistrationError(
                f"[{self._family}] Cannot register {cls.__name__}: "
                f"METHOD_ID '{key}' already claimed by {existing.__name__}. "
                f"INV-6: no silent replacement."
            )

        self._classes[key] = cls
        logger.debug(
            "AdapterRegistry[%s]: registered %s (METHOD_ID=%s PRIORITY=%s)",
            self._family, cls.__name__, key,
            getattr(cls, "PRIORITY", "NOT_SET"),
        )

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def select(self, caps) -> Type[T]:
        """
        Return highest-priority eligible reader class for caps.

        Algorithm (INV-5):
            1. Call can_handle(caps) on every registered class.
            2. Keep those returning True.
            3. Sort by PRIORITY ascending (lower wins).
            4. One winner: return it.
            5. Tie: raise ConfigurationError naming both.
            6. None: raise NoAdapterError — factory handles failure policy.

        Args:
            caps: PlatformCapabilities passed to each can_handle().

        Returns:
            Winning reader class (not instantiated).

        Raises:
            ConfigurationError  on priority tie
            NoAdapterError      when no real reader matches
        """
        eligible: List[Type[T]] = []

        for key, cls in self._classes.items():
            try:
                ok = cls.can_handle(caps)
            except Exception as exc:
                # Misbehaving can_handle must not crash startup.
                logger.warning(
                    "AdapterRegistry[%s]: %s.can_handle() raised %s — "
                    "treating as ineligible",
                    self._family, cls.__name__, exc,
                )
                ok = False

            if ok:
                eligible.append(cls)

        if not eligible:
            raise NoAdapterError(
                f"[{self._family}] No real reader passes can_handle() "
                f"for caps={caps}. "
                f"Registered: {list(self._classes.keys())}."
            )

        eligible.sort(key=lambda c: getattr(c, "PRIORITY", 999))

        winner = eligible[0]
        winner_priority = getattr(winner, "PRIORITY", 999)

        tied = [
            c for c in eligible
            if getattr(c, "PRIORITY", 999) == winner_priority
        ]

        if len(tied) > 1:
            names = ", ".join(c.__name__ for c in tied)
            raise ConfigurationError(
                f"[{self._family}] Tie at PRIORITY={winner_priority}: {names}. "
                f"Assign distinct PRIORITY values. INV-5."
            )

        logger.debug(
            "AdapterRegistry[%s]: selected %s (PRIORITY=%s) from %d eligible",
            self._family, winner.__name__, winner_priority, len(eligible),
        )
        return winner

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def get_all(self) -> dict:
        """Return copy of registry dict: METHOD_ID -> reader class."""
        return dict(self._classes)

    def is_empty(self) -> bool:
        """True if no real readers registered yet."""
        return len(self._classes) == 0

    def __repr__(self) -> str:
        return (
            f"AdapterRegistry(family={self._family!r}, "
            f"registered={list(self._classes.keys())})"
        )
