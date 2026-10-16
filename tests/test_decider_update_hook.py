"""R6 C4/C6: the goal end hook passes the committed checker result to the decider."""

import sqlite3

from core.execution.scheduled_runner import _decider_update


class _Rec:
    def __init__(self):
        self.seen = []

    def update_from_goal(self, ex, passed):
        self.seen.append(passed)


def _conn(values):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE goal_attempt (goal_id INT, pass_fail INT)")
    c.executemany("INSERT INTO goal_attempt VALUES (1, ?)", [(v,) for v in values])
    return c


def test_pass_reaches_decider():
    d = _Rec()
    assert _decider_update(_conn([0, 1]), d, 1, object()) is True
    assert d.seen == [True]


def test_fail_reaches_decider():
    d = _Rec()
    _decider_update(_conn([0, 0]), d, 1, object())
    assert d.seen == [False]


def test_unscored_teaches_none():
    d = _Rec()
    _decider_update(_conn([None]), d, 1, object())
    assert d.seen == [None]


def test_fixed_arm_skipped():
    assert _decider_update(_conn([1]), None, 1, object()) is None
