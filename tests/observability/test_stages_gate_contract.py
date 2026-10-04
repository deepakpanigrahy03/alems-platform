"""
Contract test of stages.py against the REAL 2b gate (no stubs).

test_stages.py stubs the gate with raising=False, which would hide a
missing gate function. This file fails loudly if the 2b gate does not
expose what stages.py calls, or if window refusal and event_seq do not
behave as master 5.1 rules 2 and 4 require.
"""
import pytest

from core.observability import gate
from core.observability import stages as st

REQUIRED = ("enter", "exit", "is_inside", "next_seq", "submit")


@pytest.mark.parametrize("name", REQUIRED)
def test_gate_exposes_api(name):
    """Every gate function stages.py depends on exists and is callable."""
    assert callable(getattr(gate, name, None)), "gate.%s missing" % name


def test_event_seq_monotonic_shared_counter():
    """Master 5.1 rule 4: one increasing per process counter."""
    a = gate.next_seq()
    b = gate.next_seq()
    assert isinstance(a, int) and b > a


def test_persist_refused_inside_real_window():
    """Master 5.1 rule 2 against the real gate state."""
    gate.enter()
    try:
        assert gate.is_inside()
        with pytest.raises(st.StageContractError):
            st.persist(st.StageRecorder("save_single"), lambda s, r: None)
    finally:
        gate.exit()
    assert not gate.is_inside()


def test_event_buffered_inside_window_not_lost():
    """A stage event raised inside the window gets a seq and survives exit."""
    r = st.StageRecorder("save_single")
    gate.enter()
    try:
        with r.stage("persist_run") as i:
            i["counts"] = {"runs": 1}
    finally:
        gate.exit()
    ev = r.events["persist_run"]
    assert ev["status"] == "succeeded" and isinstance(ev["event_seq"], int)
