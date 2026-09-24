"""
alems_sdk.persistence — WriterSession contract (39.2, D6.3a).

This module defines the public interface for the one logical writer per
project store (INV-21).  It contains only ABCs, dataclasses, and
exceptions.  Zero imports from core or scripts.

Conformance rule: a plugin that stores data must accept a WriterSession
handle from the runtime and never open its own connection (INV-14,
EEI-2 extension).
"""

from __future__ import annotations

import abc
from contextlib import contextmanager
from typing import Any, Dict, Generator, Iterable, List, Optional, Tuple, Union

# Re-exports kept for backward compat (39.1 persistence.py re-exported these).
# The classes themselves still live in core; they move into alems_sdk only
# when every internal caller has switched (D2.2 strangler rule).
try:
    from core.database.base import DatabaseInterface, DatabaseError  # type: ignore
    from core.extensions.abc import ExtensionABC, PostRunPayload     # type: ignore
except ImportError:
    # Allow alems_sdk to be imported in isolation (conformance kit, external
    # plugins) without the runtime installed.
    DatabaseInterface = None   # type: ignore
    DatabaseError = None       # type: ignore
    ExtensionABC = None        # type: ignore
    PostRunPayload = None      # type: ignore

__all__ = [
    # backward compat
    "DatabaseInterface",
    "DatabaseError",
    "ExtensionABC",
    "PostRunPayload",
    # new 39.2 contract
    "WriterError",
    "WriterLockError",
    "WriterSession",
]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class WriterError(Exception):
    """Base for all writer contract violations."""


class WriterLockError(WriterError):
    """Raised when the project lock cannot be acquired within the timeout.

    The message names the current lock holder (pid, host, command) so the
    researcher can identify the conflicting process without inspecting files.
    """


# ---------------------------------------------------------------------------
# WriterSession ABC
# ---------------------------------------------------------------------------

class WriterSession(abc.ABC):
    """
    Contract for the one logical writer per project store (INV-21, D6.3a).

    Lifecycle
    ---------
    1. Instantiate via the store resolver, never directly.
    2. Call open() once.  The session acquires or verifies the project lock.
    3. Use batch() as a context manager for each transactional unit.
       On __exit__ with no exception the batch commits; on exception it
       rolls back.  The session stays open after either outcome.
    4. Call close() to release resources.  If the session owns the lock it
       is released here.

    Within one process, multiple sessions on the same store path share one
    underlying connection so they cannot deadlock each other.  Across
    processes, the project lock serialises writers (section 4 of the spec).

    Ordering guarantee
    ------------------
    Writes within one session are applied in call order.  Writes inside a
    batch are atomic: all commit or none do.

    Idempotency
    -----------
    An optional idempotency_key on batch() makes a replayed batch a no-op.
    Keys are stored in the writer_idempotency plumbing table.  Not used by
    existing code in this phase; the slot is reserved for 39.7 replay.

    Read access
    -----------
    Reads do not go through this interface.  Direct SQLite reads (GUI,
    analysis scripts, validation SQL) remain legal per D6.3b.
    """

    # -- Lifecycle -----------------------------------------------------------

    @abc.abstractmethod
    def open(self) -> None:
        """
        Open the session and acquire or verify the project lock.

        Raises WriterLockError if another process holds the lock and the
        configured timeout expires.
        """

    @abc.abstractmethod
    def close(self) -> None:
        """
        Release the session.  Releases the project lock if this session
        owns it.  Safe to call more than once.
        """

    # -- Transaction ---------------------------------------------------------

    @abc.abstractmethod
    @contextmanager
    def batch(
        self,
        idempotency_key: Optional[str] = None,
    ) -> Generator[None, None, None]:
        """
        Context manager that wraps one BEGIN / COMMIT block.

        On __exit__ with no exception: COMMIT.
        On __exit__ with an exception: ROLLBACK; the exception propagates.
        The session remains usable after either outcome.

        idempotency_key: if given and already present in writer_idempotency,
        the body is skipped and the context exits normally.  The key is
        written atomically with the batch commit.
        """

    # -- Write primitives ----------------------------------------------------

    @abc.abstractmethod
    def execute(
        self,
        query: str,
        params: Union[Tuple[Any, ...], Dict[str, Any], None] = None,
    ) -> List[Dict[str, Any]]:
        """
        Execute one statement and return rows as dicts.

        Outside a batch context this auto-commits (matches SQLiteAdapter
        isolation_level=None behaviour).  Inside a batch context it
        participates in the open transaction.
        """

    @abc.abstractmethod
    def execute_many(
        self,
        query: str,
        params_list: Iterable[Union[Tuple[Any, ...], Dict[str, Any]]],
    ) -> int:
        """
        Execute one statement repeatedly.  Returns total rows affected.
        Must be called inside a batch context.
        """

    @abc.abstractmethod
    def last_row_id(self) -> Optional[int]:
        """
        Return the rowid of the last INSERT on this session's connection.

        Callers that need the id of an insert must call this inside the
        same batch context as the insert (D6.3a acknowledgement rule).
        """

    # -- Context manager convenience -----------------------------------------

    def __enter__(self) -> "WriterSession":
        """Open the session on entry."""
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """Close the session on exit regardless of outcome."""
        self.close()
        return False  # never suppress exceptions
