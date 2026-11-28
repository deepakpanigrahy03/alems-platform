"""One store connection, used by one thread at a time; other threads wait their turn (bounded)."""
import threading
import time

import pytest

from alems_sdk.persistence import WriterError
from core.storage import inprocess_writer as iw
from core.storage.inprocess_writer import InProcessWriter


@pytest.fixture
def store(tmp_path):
    path = str(tmp_path / "s.db")
    w = InProcessWriter(path)
    w.open()
    w.execute("CREATE TABLE t (v INTEGER)")
    w.close()
    return path


def _count(path):
    w = InProcessWriter(path)
    w.open()
    try:
        return w.execute("SELECT COUNT(*) AS n FROM t")[0]["n"]
    finally:
        w.close()


def test_second_thread_waits_instead_of_borrowing(store):
    held, done, errors = threading.Event(), threading.Event(), []

    def holder():
        w = InProcessWriter(store)
        w.open()
        with w.batch():
            w.execute("INSERT INTO t VALUES (1)")
            held.set()
            time.sleep(0.3)
        w.close()

    def other():
        held.wait()
        try:
            w = InProcessWriter(store)
            w.open()
            w.execute("INSERT INTO t VALUES (2)")
            w.close()
        except Exception as exc:           # before the fix: check_same_thread refusal
            errors.append(exc)
        done.set()
    a, b = threading.Thread(target=holder), threading.Thread(target=other)
    a.start(); b.start(); a.join(); b.join()
    assert errors == [] and _count(store) == 2


def test_rollback_in_one_thread_never_takes_the_other_threads_row(store):
    held, release = threading.Event(), threading.Event()

    def holder():
        w = InProcessWriter(store)
        w.open()
        try:
            with w.batch():
                w.execute("INSERT INTO t VALUES (1)")
                held.set()
                release.wait(2)
                raise RuntimeError("undo")
        except RuntimeError:
            pass
        w.close()

    def other():
        held.wait()
        release.set()
        w = InProcessWriter(store)
        w.open()
        w.execute("INSERT INTO t VALUES (2)")
        w.close()
    a, b = threading.Thread(target=holder), threading.Thread(target=other)
    a.start(); b.start(); a.join(); b.join()
    w = InProcessWriter(store)
    w.open()
    assert [r["v"] for r in w.execute("SELECT v FROM t")] == [2]
    w.close()


def test_same_thread_still_shares_one_connection(store):
    w1, w2 = InProcessWriter(store), InProcessWriter(store)
    w1.open(); w2.open()
    assert w1._adapter is w2._adapter
    w2.close(); w1.close()


def test_turn_wait_is_bounded(store, monkeypatch):
    monkeypatch.setattr(iw, "ACQUIRE_TIMEOUT_S", 0.2)
    held, release, errors = threading.Event(), threading.Event(), []

    def holder():
        w = InProcessWriter(store)
        w.open()
        held.set()
        release.wait(3)
        w.close()

    def other():
        held.wait()
        t0 = time.monotonic()
        try:
            InProcessWriter(store).open()
        except WriterError as exc:
            errors.append(time.monotonic() - t0)
        release.set()
    a, b = threading.Thread(target=holder), threading.Thread(target=other)
    a.start(); b.start(); a.join(); b.join()
    assert len(errors) == 1 and errors[0] < 2
