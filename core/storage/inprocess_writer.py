"""
core/storage/inprocess_writer.py

In-process WriterSession (D6.3, 39.2 WP-2a).

Wraps SQLiteAdapter so every runtime write path uses the WriterSession
contract without changing SQL text or transaction semantics.

Design constraints (from code survey 2026-09-23, section 3a of spec):
  - SQLiteAdapter uses isolation_level=None (autocommit).
  - db.transaction() issues an explicit BEGIN TRANSACTION.
  - Outside a batch() block, execute() auto-commits — identical to today.
  - Inside a batch() block, execute() participates in the open transaction.
  - Multiple sessions in the same process share one connection per store
    path so nested ETL calls do not self-lock.
  - Goal tracker passes raw conn objects; those call sites are unchanged
    in WP-2a.  WP-2b routes them through the session.

One logical writer per store (INV-21): the project lock is acquired at
open() and held until close().  A second process attempting to open a
write session on the same store waits up to lock_timeout_s then raises
WriterLockError naming the holder.
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, List, Optional, Tuple, Union

from alems_sdk.persistence import WriterError, WriterLockError, WriterSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Process-level connection registry (one connection per store path per process)
# ---------------------------------------------------------------------------

_registry_lock = threading.Lock()
# Maps resolved store path (str) -> (SQLiteAdapter, ref_count, owning_session_id)
_connections: Dict[str, Tuple[Any, int, int]] = {}
_next_session_id = 0


def _get_session_id() -> int:
    """Return a unique integer id for a new session."""
    global _next_session_id
    with _registry_lock:
        _next_session_id += 1
        return _next_session_id


def _acquire_connection(store_path: str) -> Any:
    """
    Return the shared SQLiteAdapter for store_path, creating it if needed.
    Increments ref count.  Thread-safe.
    """
    # Import here to avoid circular imports at module load time.
    from core.database.sqlite_adapter import SQLiteAdapter  # type: ignore

    with _registry_lock:
        if store_path in _connections:
            adapter, ref, sid = _connections[store_path]
            _connections[store_path] = (adapter, ref + 1, sid)
            return adapter
        adapter = SQLiteAdapter({"path": store_path})
        adapter.connect()
        _connections[store_path] = (adapter, 1, 0)
        logger.debug("inprocess_writer: opened connection to %s", store_path)
        return adapter


def _release_connection(store_path: str) -> None:
    """Decrement ref count; close when it reaches zero."""
    with _registry_lock:
        if store_path not in _connections:
            return
        adapter, ref, sid = _connections[store_path]
        if ref <= 1:
            try:
                adapter.close()
            except Exception:
                pass
            del _connections[store_path]
            logger.debug("inprocess_writer: closed connection to %s", store_path)
        else:
            _connections[store_path] = (adapter, ref - 1, sid)


# ---------------------------------------------------------------------------
# Project lock (file-based, per store)
# ---------------------------------------------------------------------------

class _ProjectLock:
    """
    File-based writer lock next to the store.

    Lock file path: <store_path>.writer.lock
    Content: pid, host, session_start, command_line (one per line).

    Stale detection: if the pid in the lock file is not alive on this host,
    the lock is considered stale and replaced with a warning.

    acquire() blocks up to timeout_s then raises WriterLockError.
    release() removes the file if we own it.
    """

    POLL_INTERVAL = 0.25  # seconds between retries

    def __init__(self, store_path: str, timeout_s: float = 30.0) -> None:
        self._store = store_path
        self._lock_path = Path(store_path + ".writer.lock")
        self._timeout = timeout_s
        self._owns_lock = False

    def _read_lock(self) -> Optional[Dict[str, str]]:
        """Read lock file content; return None if file absent or unreadable."""
        try:
            lines = self._lock_path.read_text().splitlines()
            if len(lines) < 3:
                return None
            return {
                "pid": lines[0].strip(),
                "host": lines[1].strip(),
                "started": lines[2].strip(),
                "cmd": lines[3].strip() if len(lines) > 3 else "",
            }
        except OSError:
            return None

    def _is_stale(self, info: Dict[str, str]) -> bool:
        """Return True if the pid in the lock is not alive on this host."""
        if info["host"] != socket.gethostname():
            # Different host — cannot check; treat as live.
            return False
        try:
            pid = int(info["pid"])
            # os.kill with signal 0 checks process existence without signalling.
            os.kill(pid, 0)
            return False  # process alive
        except ProcessLookupError:
            return True   # pid not found
        except PermissionError:
            return False  # exists but owned by another user; treat as live

    def _write_lock(self) -> None:
        """Write our lock file atomically using rename."""
        tmp = Path(str(self._lock_path) + ".tmp")
        cmd = " ".join(sys.argv[:3]) if sys.argv else "unknown"
        content = "\n".join([
            str(os.getpid()),
            socket.gethostname(),
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            cmd,
        ])
        tmp.write_text(content)
        tmp.rename(self._lock_path)

    def acquire(self) -> None:
        """
        Acquire the lock; block up to self._timeout seconds.

        If the lock file already belongs to this process, skip acquisition
        and return immediately — same process multiple sessions are allowed.
        Raises WriterLockError on timeout naming the holder.
        Sets self._owns_lock = True on success.
        """
        # Same process already holds it — share without re-acquiring.
        info = self._read_lock()
        if info and info["pid"] == str(os.getpid()):
            self._owns_lock = False  # do not release on close — other session owns it
            return
        deadline = time.monotonic() + self._timeout
        while True:
            info = self._read_lock()
            if info is None:
                # No lock file — try to create ours.
                try:
                    self._write_lock()
                    # Verify we won the race by reading back and checking pid.
                    verify = self._read_lock()
                    if verify and verify["pid"] == str(os.getpid()):
                        self._owns_lock = True
                        logger.debug(
                            "inprocess_writer: acquired lock %s", self._lock_path
                        )
                        return
                    # Lost the race — fall through to wait loop.
                except OSError:
                    pass
            elif self._is_stale(info):
                logger.warning(
                    "inprocess_writer: stale lock from pid=%s host=%s started=%s — replacing",
                    info["pid"], info["host"], info["started"],
                )
                try:
                    self._lock_path.unlink()
                except OSError:
                    pass
                continue  # retry immediately

            if time.monotonic() >= deadline:
                holder = ""
                if info:
                    holder = (
                        f"pid={info['pid']} host={info['host']} "
                        f"started={info['started']} cmd={info['cmd']}"
                    )
                raise WriterLockError(
                    f"Cannot acquire writer lock for {self._store} after "
                    f"{self._timeout}s.  Current holder: {holder or 'unknown'}"
                )
            time.sleep(self.POLL_INTERVAL)

    def release(self) -> None:
        """Remove our lock file if we own it."""
        if not self._owns_lock:
            return
        try:
            self._lock_path.unlink()
            self._owns_lock = False
            logger.debug("inprocess_writer: released lock %s", self._lock_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# InProcessWriter
# ---------------------------------------------------------------------------

class InProcessWriter(WriterSession):
    """
    WriterSession backed by SQLiteAdapter (in-process, WAL mode).

    This is the only writer implementation in foundation phases (39.1-39.5).
    The daemon-hosted writer (39.7) will implement the same WriterSession
    contract through a socket transport.

    Usage
    -----
        from core.storage.inprocess_writer import InProcessWriter

        with InProcessWriter(store_path) as w:
            with w.batch():
                w.execute("INSERT INTO runs (...) VALUES (...)", params)
                run_id = w.last_row_id()

    Or using open/close explicitly:
        w = InProcessWriter(store_path)
        w.open()
        try:
            ...
        finally:
            w.close()
    """

    def __init__(
        self,
        store_path: str,
        lock_timeout_s: float = 30.0,
    ) -> None:
        """
        Args:
            store_path:     Absolute path to the SQLite store file.
            lock_timeout_s: Seconds to wait for the project lock before
                            raising WriterLockError.
        """
        self._store_path = str(Path(store_path).resolve())
        self._session_id = _get_session_id()
        self._lock = _ProjectLock(self._store_path, timeout_s=lock_timeout_s)
        self._adapter: Optional[Any] = None
        self._open = False
        self._in_batch = False

    # -- Lifecycle -----------------------------------------------------------

    def open(self) -> None:
        """Acquire the project lock and open (or share) the connection."""
        if self._open:
            return
        self._lock.acquire()
        self._adapter = _acquire_connection(self._store_path)
        self._open = True
        logger.debug(
            "InProcessWriter session_id=%d opened store=%s",
            self._session_id, self._store_path,
        )

    def close(self) -> None:
        """Release the connection ref-count and the project lock."""
        if not self._open:
            return
        _release_connection(self._store_path)
        self._lock.release()
        self._adapter = None
        self._open = False
        logger.debug(
            "InProcessWriter session_id=%d closed store=%s",
            self._session_id, self._store_path,
        )

    # -- Transaction ---------------------------------------------------------

    @contextmanager
    def batch(
        self,
        idempotency_key: Optional[str] = None,
    ) -> Generator[None, None, None]:
        """
        Context manager for one BEGIN / COMMIT block.

        Reproduces the same isolation_level=None + explicit BEGIN TRANSACTION
        semantics as SQLiteAdapter.transaction() (section 3a of spec).
        """
        if not self._open:
            raise WriterError("WriterSession is not open.  Call open() first.")
        if self._in_batch:
            # Nested batch: participate in the outer transaction rather than
            # nesting BEGIN TRANSACTION (SQLite does not support savepoints
            # here; callers must not rely on inner-batch rollback isolation).
            logger.debug(
                "InProcessWriter session_id=%d: nested batch — sharing outer transaction",
                self._session_id,
            )
            yield
            return

        conn = self._adapter.conn
        conn.execute("BEGIN TRANSACTION")
        self._in_batch = True
        try:
            yield
            conn.commit()
            logger.debug(
                "InProcessWriter session_id=%d: batch committed", self._session_id
            )
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            logger.debug(
                "InProcessWriter session_id=%d: batch rolled back", self._session_id
            )
            raise
        finally:
            self._in_batch = False

    # -- Write primitives ----------------------------------------------------

    def execute(
        self,
        query: str,
        params: Union[Tuple[Any, ...], Dict[str, Any], None] = None,
    ) -> List[Dict[str, Any]]:
        """
        Execute one statement.  Outside a batch, auto-commits.
        Inside a batch, participates in the open transaction.
        """
        if not self._open:
            raise WriterError("WriterSession is not open.")
        return self._adapter.execute(query, params)

    def execute_many(
        self,
        query: str,
        params_list: Iterable[Union[Tuple[Any, ...], Dict[str, Any]]],
    ) -> int:
        """Execute one statement repeatedly.  Must be inside a batch."""
        if not self._open:
            raise WriterError("WriterSession is not open.")
        if not self._in_batch:
            raise WriterError(
                "execute_many must be called inside a batch() context."
            )
        return self._adapter.execute_many(query, list(params_list))

    def last_row_id(self) -> Optional[int]:
        """Return lastrowid of the most recent INSERT on this connection."""
        if not self._open or self._adapter is None:
            return None
        try:
            return self._adapter.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        except Exception:
            return None

    # -- Idempotency helpers -------------------------------------------------

    def _idempotency_seen(self, key: str) -> bool:
        """Return True if this idempotency key was already committed."""
        try:
            rows = self._adapter.execute(
                "SELECT 1 FROM writer_idempotency WHERE idem_key = ? LIMIT 1",
                (key,),
            )
            return len(rows) > 0
        except Exception:
            # Table may not exist yet on an old store; treat as not seen.
            return False

    def _record_idempotency(self, key: str) -> None:
        """Insert the idempotency key inside the current open transaction."""
        try:
            self._adapter.conn.execute(
                "INSERT OR IGNORE INTO writer_idempotency (idem_key, recorded_at) "
                "VALUES (?, datetime('now'))",
                (key,),
            )
        except Exception as exc:
            logger.warning(
                "InProcessWriter: failed to record idempotency key %s: %s", key, exc
            )

    # -- Expose raw connection for legacy callers (WP-2b transition) ---------

    @property
    def conn(self) -> Any:
        """
        Raw sqlite3 connection.

        Used only during the WP-2b transition period for callers (goal_tracker,
        harness) that still pass raw conn objects.  Do not use in new code.
        Will be removed after WP-2b completes.
        """
        if self._adapter is None:
            raise WriterError("Session not open.")
        return self._adapter.conn
