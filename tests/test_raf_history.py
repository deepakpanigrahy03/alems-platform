"""RAF R6 history (design v2 section 10, core tests)."""

import json
import sqlite3
from contextlib import contextmanager

from core.execution.raf_history import count_key, read_history, write_decisions

POP = {"params_version": "p1", "data_tag": "d1", "decider": "beta_p",
       "arm": "ctrl", "study_id": "e3"}


def _conn():
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE goal_attempt (attempt_id INTEGER PRIMARY KEY, goal_id INT,
            attempt_number INT, span_id TEXT, pass_fail INT);
        CREATE TABLE span_annotations (annotation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            span_id TEXT NOT NULL, annotation_type TEXT NOT NULL,
            annotation_version TEXT, source TEXT, payload TEXT NOT NULL);
    """)
    return c


def _goal(c, goal_id, attempts, passed):
    """attempts spans s<goal>_<n>; pass_fail on the last attempt (None: unscored)."""
    for n in range(1, attempts + 1):
        pf = passed if n == attempts else (0 if passed is not None else None)
        c.execute("INSERT INTO goal_attempt (goal_id, attempt_number, span_id, pass_fail) "
                  "VALUES (?,?,?,?)", (goal_id, n, "s%d_%d" % (goal_id, n), pf))


def _dec(c, goal_id, att, option, cls="timeout", **pop):
    p = dict(POP, **pop)
    p.update(decision_attempt=att, option=option, failure_class=cls)
    c.execute("INSERT INTO span_annotations (span_id, annotation_type, payload) "
              "VALUES (?, 'raf.controller', ?)", ("s%d_%d" % (goal_id, att - 1), json.dumps(p)))


def test_success_credits_only_last_option():
    c = _conn()
    _goal(c, 1, 3, 1)
    _dec(c, 1, 2, "SR_warm")
    _dec(c, 1, 3, "FR_cold")
    h = read_history(c, POP)
    assert h["counts"] == {count_key("timeout", "SR_warm"): [0, 1],
                           count_key("timeout", "FR_cold"): [1, 0]}


def test_failed_and_abandoned_goals_fail_every_option():
    c = _conn()
    _goal(c, 1, 2, 0)
    _dec(c, 1, 2, "SR_warm")
    _goal(c, 2, 3, 0)
    _dec(c, 2, 2, "RB_warm")
    _dec(c, 2, 3, "ABANDON")
    h = read_history(c, POP)
    assert h["counts"] == {count_key("timeout", "SR_warm"): [0, 1],
                           count_key("timeout", "RB_warm"): [0, 1]}


def test_null_outcome_is_no_evidence():
    c = _conn()
    _goal(c, 1, 2, None)
    _dec(c, 1, 2, "SR_warm")
    h = read_history(c, POP)
    assert h["counts"] == {} and h["goals"] == 0


def test_duplicates_do_not_inflate():
    c = _conn()
    _goal(c, 1, 2, 1)
    _dec(c, 1, 2, "SR_warm")
    _dec(c, 1, 2, "SR_warm")
    assert read_history(c, POP)["counts"] == {count_key("timeout", "SR_warm"): [1, 0]}


def test_other_populations_excluded():
    c = _conn()
    for gid, over in enumerate([{"arm": "other"}, {"decider": "laya_p"},
                                {"params_version": "p0"}, {"study_id": "deploy"},
                                {"data_tag": "d0"}], start=1):
        _goal(c, gid, 2, 1)
        _dec(c, gid, 2, "SR_warm", **over)
    assert read_history(c, POP)["counts"] == {}


def test_pre_e3_and_unrelated_goals_stay_out():
    c = _conn()
    # Started before E3 (deploy decision), finished during E3.
    _goal(c, 1, 2, 1)
    _dec(c, 1, 2, "SR_warm", study_id="deploy")
    # Unrelated goal, same params, other study.
    _goal(c, 2, 2, 1)
    _dec(c, 2, 2, "SR_warm", study_id="e1b_probe")
    # One E3 goal.
    _goal(c, 3, 2, 0)
    _dec(c, 3, 2, "FR_cold")
    assert read_history(c, POP)["counts"] == {count_key("timeout", "FR_cold"): [0, 1]}


def test_cut_id_and_recompute_equal_recorded():
    c = _conn()
    _goal(c, 1, 2, 1)
    _dec(c, 1, 2, "SR_warm")
    rec = read_history(c, POP)
    _goal(c, 2, 2, 0)
    _dec(c, 2, 2, "SR_warm")                      # arrives after the decision read
    again = read_history(c, POP, cut_id=rec["cut_id"])
    assert rec["cut_id"] == 1 and again["counts"] == rec["counts"]
    assert read_history(c, POP)["counts"] == {count_key("timeout", "SR_warm"): [1, 1]}


def test_current_goal_excluded():
    c = _conn()
    _goal(c, 1, 2, 1)
    _dec(c, 1, 2, "SR_warm")
    assert read_history(c, POP, exclude_goal_id=1)["counts"] == {}


class _Db:
    def __init__(self, conn):
        self.conn, self.rows = conn, []

    @contextmanager
    def transaction(self):
        yield

    def insert_span_annotations(self, rows):
        self.rows.extend(rows)


def test_write_decisions_anchor_and_filter():
    c = _conn()
    _goal(c, 1, 2, 1)
    db = _Db(c)
    decisions = {
        2: dict(POP, option="SR_warm", failure_class="timeout"),
        3: dict(POP, option="FR_cold", failure_class="timeout"),   # attempt 2 span exists
        4: dict(POP, option="RB_warm", failure_class="timeout"),   # attempt 3 missing
    }
    decisions[5] = {"chosen": "FR_cold", "policy": "FR_cold", "ns": 1}   # escalate_stub
    written, skipped = write_decisions(db, c, 1, decisions)
    assert (written, skipped) == (2, 1)
    assert [r["span_id"] for r in db.rows] == ["s1_1", "s1_2"]
    assert db.rows[0]["payload"]["decision_attempt"] == 2
    assert all(r["annotation_type"] == "raf.controller" for r in db.rows)
