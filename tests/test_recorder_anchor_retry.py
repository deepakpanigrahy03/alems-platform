"""A refused clock anchor write is kept and written with the next anchor, exactly once."""
import threading

from extensions.thermal_exposure.recorder import ExperimentRecorder


class _Clock(object):
    def __init__(self):
        self.t = 0

    def wall_ns(self):
        self.t += 10 ** 9
        return self.t

    def mono_ns(self):
        return self.t


def _recorder(results):
    rec = ExperimentRecorder.__new__(ExperimentRecorder)
    rec.clock, rec._base_wall, rec._base_mono = _Clock(), 0, 0
    rec.offset_fn = lambda: (None, None)
    rec._lock, rec._anchors, rec._pending_anchors = threading.Lock(), [], []
    rec.session_id, rec.node_name = 7, "local"
    written = []

    def write_rows(sql, rows):
        if results.pop(0) == "refuse":
            raise RuntimeError("SQLite objects created in a thread can only be used in that same thread")
        written.extend(rows)
    rec._write_rows = write_rows
    return rec, written


def test_refused_anchor_is_written_with_the_next_one():
    rec, written = _recorder(["refuse", "ok"])
    rec._write_anchor(None)
    assert written == [] and len(rec._pending_anchors) == 1
    rec._write_anchor(0)
    assert len(written) == 2 and rec._pending_anchors == []
    assert written[0][2] < written[1][2]            # oldest first, each once


def test_anchors_kept_across_several_refusals():
    rec, written = _recorder(["refuse", "refuse", "ok"])
    for _ in range(3):
        rec._write_anchor(0)
    assert len(written) == 3 and len({r[2] for r in written}) == 3
