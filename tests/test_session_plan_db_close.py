"""A plan session closes the store it opened, so no writer lock is left behind."""
from core.execution.session_plan import _Runtime


class _Db(object):
    closed = False

    def close(self):
        self.closed = True


class _Runner(object):
    def __init__(self, fail=False):
        self.fail, self.updates = fail, []

    def update_status(self, db, exp_id, status, goals):
        if self.fail:
            raise RuntimeError("store busy")
        self.updates.append((exp_id, status, goals))


def _rt(runner):
    rt = _Runtime.__new__(_Runtime)
    rt.runner, rt.db, rt.units = runner, _Db(), {("a", "p"): {"exp_id": 5, "goals": 2}}
    return rt


def test_finish_marks_rows_and_closes_the_store():
    rt = _rt(_Runner())
    db = rt.db
    rt.finish()
    assert rt.runner.updates == [(5, "completed", 2)] and db.closed and rt.db is None


def test_store_closed_even_when_the_status_update_fails():
    rt = _rt(_Runner(fail=True))
    db = rt.db
    try:
        rt.finish()
    except RuntimeError:
        pass
    assert db.closed and rt.db is None


def test_finish_without_an_opened_store_is_fine():
    rt = _rt(_Runner())
    rt.db, rt.units = None, {}
    rt.finish()
