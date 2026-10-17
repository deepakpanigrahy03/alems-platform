"""make_pilot_config reproduces E1's tasks block from the rule (no hand edits)."""

import os
import sys

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import make_pilot_config as mp  # noqa: E402

SANDBOX = os.path.expanduser("~/sandboxes/raf-mlsys-2027")
SWEEP = os.path.join(SANDBOX, "raf_sweep30", "sweep_r1.txt")
E1_TASKS = os.path.join(SANDBOX, "raf_e1", "e1_tasks.txt")


def test_late_rule():
    assert mp.late(8) == 6 and mp.late(6) == 4 and mp.late(15) == 10   # round(10.5) = 10, as E1
    assert mp.late(4) == 3 and mp.late(5) == 4                          # clamp [3, n-1]


@pytest.mark.skipif(not (os.path.exists(SWEEP) and os.path.exists(E1_TASKS)), reason="sandbox data absent")
def test_reproduces_e1_tasks_block():
    with open(os.path.join(HERE, "..", "e1_gn100.yaml")) as fh:
        e1 = yaml.safe_load(fh)["tasks"]
    with open(E1_TASKS) as fh:
        ids = [l.strip() for l in fh if l.strip()]
    assert mp.tasks_block(ids, mp.sweep_counts(SWEEP)) == e1
