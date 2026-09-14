#!/usr/bin/env python3
"""
================================================================================
PLATFORM REGISTRY  —  core/platform/registry.py
================================================================================

Purpose:
    Registry for platform adapters. Calls detect() in PRIORITY order.
    First adapter whose detect() returns non-None wins.
    Exposes the active adapter for provision() and verify() calls.

    Uses AdapterRegistry from 35A for registration and duplicate detection.
    Selection is custom (detect() probe loop) not can_handle() based.

Author: Deepak Panigrahy
Spec:   SPEC 35C, Part A
================================================================================
"""

import logging
from typing import Any, Dict, List, Optional, Type

from core.platform.adapter import PlatformAdapterABC, ProvisionResult, VerificationResult

logger = logging.getLogger(__name__)


class DuplicatePlatformError(Exception):
    """Two adapters claim the same PLATFORM_CLASS. INV-6."""


class NoPlatformDetectedError(Exception):
    """No adapter's detect() returned non-None. Should never happen — SyntheticAdapter is always last resort."""


class PlatformRegistry:
    """
    Registry for platform adapters.

    Registration: one adapter class per PLATFORM_CLASS.
    Selection: call detect() in PRIORITY order, first non-None wins.
    Active adapter: cached after first detection, used for provision/verify.

    Usage:
        registry = PlatformRegistry()
        registry.register(NVIDIAGraceAdapter)
        registry.register(IntelLinuxAdapter)
        hw_config = registry.detect()
        result = registry.provision()
        result = registry.verify()
    """

    def __init__(self) -> None:
        # PLATFORM_CLASS -> adapter class
        self._classes: Dict[str, Type[PlatformAdapterABC]] = {}
        # Cached active adapter instance after detect() runs
        self._active: Optional[PlatformAdapterABC] = None
        # Cached hw_config from winning detect()
        self._hw_config: Optional[Dict[str, Any]] = None

    def register(self, cls: Type[PlatformAdapterABC]) -> None:
        """
        Register a platform adapter class.

        Args:
            cls: PlatformAdapterABC subclass with PLATFORM_CLASS and PRIORITY.

        Raises:
            DuplicatePlatformError: if PLATFORM_CLASS already registered (INV-6).
        """
        key = cls.PLATFORM_CLASS
        if key in self._classes:
            existing = self._classes[key]
            raise DuplicatePlatformError(
                f"Cannot register {cls.__name__}: "
                f"PLATFORM_CLASS '{key}' already claimed by {existing.__name__}. "
                f"INV-6: no silent replacement."
            )
        self._classes[key] = cls
        logger.debug(
            "PlatformRegistry: registered %s (PLATFORM_CLASS=%s PRIORITY=%s)",
            cls.__name__, key, cls.PRIORITY,
        )

    def detect(self) -> Dict[str, Any]:
        """
        Call detect() on all adapters in PRIORITY order.
        First non-None result wins. Active adapter cached for provision/verify.

        Returns:
            hw_config dict from the winning adapter.

        Raises:
            NoPlatformDetectedError: if no adapter returns non-None.
        """
        if self._hw_config is not None:
            return self._hw_config

        # SPEC 35C: synthetic override bypasses all real adapters.
        # Check before priority-ordered detection so real hardware
        # adapters never run when synthetic is explicitly requested.
        import os
        if os.environ.get("ALEMS_PLATFORM_OVERRIDE", "").strip().lower() == "synthetic":
            if "synthetic" in self._classes:
                adapter = self._classes["synthetic"]()
                result = adapter.detect()
                if result is not None:
                    self._active    = adapter
                    self._hw_config = result
                    return result

        # Sort by PRIORITY ascending (lower = runs first)
        ordered = sorted(self._classes.values(), key=lambda c: c.PRIORITY)

        for cls in ordered:
            adapter = cls()
            logger.debug(
                "PlatformRegistry: trying %s (PRIORITY=%s)",
                cls.__name__, cls.PRIORITY,
            )
            try:
                result = adapter.detect()
            except Exception as exc:
                logger.warning(
                    "PlatformRegistry: %s.detect() raised %s — skipping",
                    cls.__name__, exc,
                )
                result = None

            if result is not None:
                logger.info(
                    "PlatformRegistry: detected platform via %s (PLATFORM_CLASS=%s)",
                    cls.__name__, cls.PLATFORM_CLASS,
                )
                self._active   = adapter
                self._hw_config = result
                return result

        raise NoPlatformDetectedError(
            f"No platform adapter detected this machine. "
            f"Registered: {list(self._classes.keys())}. "
            f"SyntheticAdapter should always be last resort — check registration."
        )

    def provision(self) -> ProvisionResult:
        """
        Run provision() on the active adapter.
        Must call detect() first.

        Returns:
            ProvisionResult from the active platform adapter.
        """
        if self._active is None:
            self.detect()
        return self._active.provision()

    def verify(self) -> VerificationResult:
        """
        Run verify() on the active adapter.
        Must call detect() first.

        Returns:
            VerificationResult from the active platform adapter.
        """
        if self._active is None:
            self.detect()
        return self._active.verify()

    @property
    def active_platform_class(self) -> Optional[str]:
        """Return PLATFORM_CLASS of the active adapter, or None if not detected yet."""
        return self._active.PLATFORM_CLASS if self._active else None

    def get_all(self) -> Dict[str, Type[PlatformAdapterABC]]:
        """Return copy of registry for inspection."""
        return dict(self._classes)

    def is_empty(self) -> bool:
        """True if no adapters registered."""
        return len(self._classes) == 0
