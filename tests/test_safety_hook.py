"""
Tests for core/safety/hook.py (process guard, halting, run boundary notices)
and core/safety/store_sink.py (queued writes in order, idempotent retries),
with fake guards and an in-memory store built from migration v133.
"""
import sqlite3
import threading
from pathlib import Path

import pytest

from core.safety import hook
from core.safety.store_sink import StoreSink
from core.safety.thermal_guard import GuardRefused, ThermalGuard, reading

REPO = Path(__file__).resolve().parents[1]
CFG = {
    "policy_version": "1", "enabled": True, "interval_s": 1.0,
    "start_floor_headroom_c": 28.0, "start_idle_margin_c": None, "start_stable_s": 30.0,
    "max_wait_s": 600.0, "stop_headroom_c": 15.0, "min_rest_s": 30.0, "stale_after_s": 3.0,
    "allow_without_source": False, "rise_warn_c_per_s": None,
}


class FakeGuard(object):
    def __init__(self, refuse_open=None, refuse_admit=None):
        self.episode = {"episode_id": "ep", "latch_reason": None}
        self.calls = []
        self.active = 0
        self._store = None
        self.refuse_open, self.refuse_admit = refuse_open, refuse_admit

    def open(self):
        self.calls.append("open")
        if self.refuse_open:
            raise GuardRefused(self.refuse_open)

    def start_watchdog(self):
        self.calls.append("watchdog")

    def admit(self, requested_rest_s=0.0, links=None):
        self.calls.append(("admit", links.get("kind")))
        if self.refuse_admit:
            raise GuardRefused(self.refuse_admit)

    def active_count(self):
        return self.active

    def workload_started(self, links=None):
        self.active += 1

    def workload_ended(self, links=None):
        self.active = max(0, self.active - 1)
        self.calls.append("ended")

    def close(self, reason):
        self.calls.append(("close", reason))


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    hook.reset()
    monkeypatch.setattr(hook, "load_settings", lambda: dict(CFG))
    yield
    hook.set_factory(None)
    hook.reset()


def use(guard):
    made = []

    def factory(cfg, entry):
        made.append(entry)
        return guard
    hook.set_factory(factory)
    return made


def test_disabled_by_config_opens_nothing(monkeypatch):
    monkeypatch.setattr(hook, "load_settings", lambda: dict(CFG, enabled=False))
    made = use(FakeGuard())
    hook.before_run("linear")
    assert made == [] and hook.halted() is None


def test_missing_block_fails_closed(monkeypatch):
    monkeypatch.setattr(hook, "load_settings", lambda: None)
    with pytest.raises(GuardRefused) as e:
        hook.before_run("linear")
    assert e.value.code == "config_missing" and hook.halted() == "config_missing"


def test_invalid_config_fails_closed():
    def factory(cfg, entry):
        return ThermalGuard(dict(cfg, min_rest_s="x"), [], lambda r: True)
    hook.set_factory(factory)
    with pytest.raises(GuardRefused) as e:
        hook.before_run("linear")
    assert e.value.code == "guard_unavailable"


def test_refusal_halts_every_later_call_without_reopening():
    made = use(FakeGuard(refuse_open="no_source"))
    with pytest.raises(GuardRefused):
        hook.before_run("linear")
    with pytest.raises(GuardRefused) as e:
        hook.before_run("agentic")
    assert e.value.code == "no_source" and made == ["harness"]


def test_gate_refusal_halts():
    g = FakeGuard(refuse_admit="idle_headroom_insufficient")
    use(g)
    with pytest.raises(GuardRefused):
        hook.before_run("linear")
    assert hook.halted() == "idle_headroom_insufficient"
    with pytest.raises(GuardRefused):
        hook.before_run("linear")
    assert g.calls.count(("admit", "linear")) == 1


def test_open_once_and_admit_per_run():
    g = FakeGuard()
    made = use(g)
    hook.before_run("linear")
    hook.before_run("agentic")
    assert made == ["harness"]
    assert g.calls == ["open", "watchdog", ("admit", "linear"), ("admit", "agentic")]


def test_run_without_end_notice_counts_as_ended_at_next_admission():
    g = FakeGuard()
    use(g)
    hook.before_run("linear")
    hook.workload_started()
    hook.before_run("agentic")
    assert g.active == 0 and "ended" in g.calls


def test_recording_boundary_notices_reach_the_guard():
    from core.recording import hook as rec_hook
    g = FakeGuard()
    use(g)
    hook.before_run("linear")
    rec_hook.notify_run_start()
    assert g.active == 1
    rec_hook.notify_run_end()
    assert g.active == 0


def test_boundary_notices_without_guard_do_nothing():
    from core.recording import hook as rec_hook
    rec_hook.notify_run_start()
    rec_hook.notify_run_end()
    assert hook.halted() is None


def test_close_ends_episode():
    g = FakeGuard()
    use(g)
    hook.before_run("linear")
    hook.close("completed")
    assert ("close", "completed") in g.calls


# ---------------------------------------------------------------- store sink
class MemWriter(object):
    def __init__(self, conn, fail=False):
        self.conn, self.fail = conn, fail

    def open(self):
        pass

    def close(self):
        pass

    def batch(self):
        conn, fail = self.conn, self.fail

        class _B(object):
            def __enter__(self):
                conn.execute("BEGIN")

            def __exit__(self, et, ev, tb):
                if et is None and not fail:
                    conn.commit()
                    return False
                conn.rollback()
                if fail and et is None:
                    raise IOError("busy")
                return False
        return _B()

    def execute(self, sql, params):
        self.conn.execute(sql, params)


def _store():
    conn = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)  # sink writes from its own thread, as the store writer allows
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript((REPO / "migrations/schema/v133_safety_guard.sql").read_text())
    return conn


class Clock(object):
    def __init__(self):
        self.mono = 1_000_000_000

    def mono_ns(self):
        return self.mono

    def utc_ns(self):
        return self.mono

    def sleep(self, s):
        self.mono += int(s * 1e9)


class Src(object):
    name = "fake"

    def __init__(self, v):
        self.v = v

    def read(self, now):
        return [reading("fake", "gpu0", "slowdown_margin", self.v, "fresh", now)]


def test_sink_writes_episode_events_samples_in_order_and_retries_idempotently():
    """Headroom 14 at open: a stop-level crossing is recorded (never a stop)."""
    conn = _store()
    state = {"fail": True}
    sink = StoreSink(lambda: MemWriter(conn, fail=state["fail"]))
    clock = Clock()
    g = ThermalGuard(dict(CFG), [Src(14.0)], lambda r: True, sink, entry_path="test",
                     mono_ns=clock.mono_ns, utc_ns=clock.utc_ns, sleep=clock.sleep)
    g.open()                        # headroom 14 at open: crossing recorded
    assert not sink.flush()         # store busy: everything kept
    assert conn.execute("SELECT COUNT(*) FROM safety_guard_events").fetchone()[0] == 0
    state["fail"] = False
    assert sink.flush()
    assert sink.flush()             # nothing left; a second flush is a no-op
    ev = [r[0] for r in conn.execute("SELECT event_type FROM safety_guard_events ORDER BY seq")]
    assert ev[0] == "episode_start" and "warn" in ev and "stop" not in ev
    assert conn.execute("SELECT status, latched FROM safety_guard_episodes").fetchone() == ("running", 0)
    assert conn.execute("SELECT COUNT(*) FROM safety_guard_samples").fetchone()[0] >= 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_sink_flushes_only_between_runs():
    conn = _store()
    sink = StoreSink(lambda: MemWriter(conn))
    clock = Clock()
    g = ThermalGuard(dict(CFG, interval_s=0.05), [Src(33.0)], lambda r: True, sink, entry_path="test",
                     mono_ns=clock.mono_ns, utc_ns=clock.utc_ns, sleep=clock.sleep)
    g.open()
    g.workload_started()
    sink.attach(g)
    threading.Event().wait(0.3)
    assert conn.execute("SELECT COUNT(*) FROM safety_guard_episodes").fetchone()[0] == 0
    g.workload_ended()
    threading.Event().wait(0.3)
    assert conn.execute("SELECT COUNT(*) FROM safety_guard_episodes").fetchone()[0] == 1
    sink.close()
