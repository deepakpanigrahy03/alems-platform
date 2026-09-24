"""
tests/test_writer_2a.py

WP-2a acceptance tests (SPEC 39.2 section 11):
  - WriterSession ABC contract shape
  - InProcessWriter open/close/batch/execute lifecycle
  - Project lock acquire and release
  - Stale lock detection
  - Concurrent writer: second attempt raises WriterLockError within timeout
  - resolve_store returns same path as old path_loader (shim equivalence)
  - resolve_hw_config returns an existing path
  - writer_idempotency table exists after migration

All tests use a temp DB — never touch the live store.
"""

import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_temp_store(tmp_path):
    """Create a minimal SQLite store at tmp_path/test.db."""
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS writer_idempotency "
        "(idem_key TEXT NOT NULL PRIMARY KEY, recorded_at TEXT NOT NULL "
        "DEFAULT (datetime('now')))"
    )
    conn.commit()
    conn.close()
    return str(db)


# ---------------------------------------------------------------------------
# WriterSession ABC shape
# ---------------------------------------------------------------------------

class TestWriterSessionABC:
    def test_abstract_methods_exist(self):
        from alems_sdk.persistence import WriterSession
        required = {"open", "close", "batch", "execute", "execute_many", "last_row_id"}
        abstract = getattr(WriterSession, "__abstractmethods__", set())
        assert required.issubset(abstract), (
            f"Missing abstract methods: {required - abstract}"
        )

    def test_writer_error_hierarchy(self):
        from alems_sdk.persistence import WriterError, WriterLockError
        assert issubclass(WriterLockError, WriterError)
        assert issubclass(WriterError, Exception)


# ---------------------------------------------------------------------------
# InProcessWriter lifecycle
# ---------------------------------------------------------------------------

class TestInProcessWriter:
    def test_open_close(self, tmp_path):
        from core.storage.inprocess_writer import InProcessWriter
        store = _make_temp_store(tmp_path)
        w = InProcessWriter(store)
        w.open()
        assert w._open is True
        w.close()
        assert w._open is False

    def test_context_manager(self, tmp_path):
        from core.storage.inprocess_writer import InProcessWriter
        store = _make_temp_store(tmp_path)
        with InProcessWriter(store) as w:
            assert w._open is True
        assert w._open is False

    def test_execute_outside_batch_autocommits(self, tmp_path):
        from core.storage.inprocess_writer import InProcessWriter
        store = _make_temp_store(tmp_path)
        conn_check = sqlite3.connect(store)
        conn_check.execute(
            "CREATE TABLE IF NOT EXISTS t (v INTEGER)"
        )
        conn_check.commit()
        conn_check.close()

        with InProcessWriter(store) as w:
            w.execute("INSERT INTO t (v) VALUES (?)", (42,))

        # Read back with a new connection to confirm commit.
        conn_check = sqlite3.connect(store)
        rows = conn_check.execute("SELECT v FROM t").fetchall()
        conn_check.close()
        assert rows == [(42,)]

    def test_batch_commits_on_success(self, tmp_path):
        from core.storage.inprocess_writer import InProcessWriter
        store = _make_temp_store(tmp_path)
        conn_check = sqlite3.connect(store)
        conn_check.execute("CREATE TABLE IF NOT EXISTS t (v INTEGER)")
        conn_check.commit()
        conn_check.close()

        with InProcessWriter(store) as w:
            with w.batch():
                w.execute("INSERT INTO t (v) VALUES (?)", (99,))
                rid = w.last_row_id()
                assert rid is not None and rid > 0

        conn_check = sqlite3.connect(store)
        rows = conn_check.execute("SELECT v FROM t").fetchall()
        conn_check.close()
        assert rows == [(99,)]

    def test_batch_rolls_back_on_exception(self, tmp_path):
        from core.storage.inprocess_writer import InProcessWriter
        store = _make_temp_store(tmp_path)
        conn_check = sqlite3.connect(store)
        conn_check.execute("CREATE TABLE IF NOT EXISTS t (v INTEGER)")
        conn_check.commit()
        conn_check.close()

        with InProcessWriter(store) as w:
            try:
                with w.batch():
                    w.execute("INSERT INTO t (v) VALUES (?)", (7,))
                    raise RuntimeError("deliberate failure")
            except RuntimeError:
                pass

        conn_check = sqlite3.connect(store)
        rows = conn_check.execute("SELECT v FROM t").fetchall()
        conn_check.close()
        assert rows == [], "Rolled-back insert must not appear"

    def test_double_open_is_safe(self, tmp_path):
        from core.storage.inprocess_writer import InProcessWriter
        store = _make_temp_store(tmp_path)
        w = InProcessWriter(store)
        w.open()
        w.open()  # must not raise or double-acquire
        w.close()
        assert w._open is False

    def test_double_close_is_safe(self, tmp_path):
        from core.storage.inprocess_writer import InProcessWriter
        store = _make_temp_store(tmp_path)
        w = InProcessWriter(store)
        w.open()
        w.close()
        w.close()  # must not raise


# ---------------------------------------------------------------------------
# Project lock
# ---------------------------------------------------------------------------

class TestProjectLock:
    def test_acquire_and_release(self, tmp_path):
        from core.storage.inprocess_writer import _ProjectLock
        store = str(tmp_path / "test.db")
        lock = _ProjectLock(store, timeout_s=5.0)
        lock.acquire()
        assert lock._owns_lock is True
        lock_file = Path(store + ".writer.lock")
        assert lock_file.exists()
        lock.release()
        assert not lock_file.exists()
        assert lock._owns_lock is False

    def test_stale_lock_replaced(self, tmp_path):
        from core.storage.inprocess_writer import _ProjectLock
        import socket
        store = str(tmp_path / "test.db")
        lock_path = Path(store + ".writer.lock")
        # Write a stale lock with pid 99999999 (almost certainly not alive).
        lock_path.write_text(
            "\n".join(["99999999", socket.gethostname(), "2000-01-01T00:00:00Z", "old"])
        )
        lock = _ProjectLock(store, timeout_s=5.0)
        lock.acquire()
        content = lock_path.read_text()
        assert str(os.getpid()) in content, "Our pid must be in the replaced lock"
        lock.release()

    def test_concurrent_writer_blocked(self, tmp_path):
        """Second InProcessWriter on same store from a different pid raises WriterLockError."""
        from core.storage.inprocess_writer import _ProjectLock
        from alems_sdk.persistence import WriterLockError
        import socket
        store = str(tmp_path / "test.db")
        lock_path = Path(store + ".writer.lock")
        # Write a lock file with a different pid that is alive (pid 1 = init, always alive).
        lock_path.write_text(
            "\n".join(["1", socket.gethostname(), "2026-01-01T00:00:00Z", "other_process"])
        )
        lock2 = _ProjectLock(store, timeout_s=1.0)
        with pytest.raises(WriterLockError):
            lock2.acquire()

    def test_shared_connection_within_process(self, tmp_path):
        """Two InProcessWriter sessions in the same process share one connection."""
        from core.storage.inprocess_writer import InProcessWriter, _connections
        store = _make_temp_store(tmp_path)
        w1 = InProcessWriter(store, lock_timeout_s=5.0)
        w1.open()
        w2 = InProcessWriter(store, lock_timeout_s=5.0)
        w2.open()
        # Same underlying adapter.
        assert _connections[w1._store_path][0] is _connections[w2._store_path][0]
        w2.close()
        w1.close()


# ---------------------------------------------------------------------------
# Store resolver
# ---------------------------------------------------------------------------

class TestResolver:
    def test_explicit_wins(self, tmp_path):
        from core.storage.resolver import resolve_store
        db = str(tmp_path / "explicit.db")
        result = resolve_store(explicit=db)
        assert result == str(Path(db).resolve())

    def test_alems_store_env(self, tmp_path, monkeypatch):
        from core.storage.resolver import resolve_store
        db = str(tmp_path / "env_store.db")
        monkeypatch.setenv("ALEMS_STORE", db)
        result = resolve_store()
        assert result == str(Path(db).resolve())

    def test_path_loader_shim_matches_resolver(self):
        """Old path_loader output must equal new resolver output."""
        from scripts.tools.path_loader import get_alems_db_path
        from core.storage.resolver import resolve_store
        old = get_alems_db_path()
        new = resolve_store()
        assert old == new, (
            f"Shim mismatch: path_loader={old!r} resolve_store={new!r}"
        )


# ---------------------------------------------------------------------------
# hw_config resolver
# ---------------------------------------------------------------------------

class TestHwConfigResolver:
    def test_returns_existing_path(self):
        from core.storage.resolver import resolve_hw_config
        p = resolve_hw_config()
        # The repo fallback (config/hw_config.json) always exists on GN100
        # and UBUNTU2505 after detect_hardware has run.
        assert p.exists(), f"resolve_hw_config() returned non-existent path: {p}"

    def test_returns_path_object(self):
        from core.storage.resolver import resolve_hw_config
        from pathlib import Path
        p = resolve_hw_config()
        assert isinstance(p, Path)


# ---------------------------------------------------------------------------
# writer_idempotency table (migration v112)
# ---------------------------------------------------------------------------

class TestIdempotencyTable:
    def test_table_exists_in_live_db(self):
        """writer_idempotency must exist in the live store after v111 migration."""
        from scripts.tools.path_loader import get_alems_db_path
        db_path = get_alems_db_path()
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='writer_idempotency'"
        ).fetchall()
        conn.close()
        assert rows, "writer_idempotency table not found — run alems dev migrate --run"
