"""--items selection for scheduled profiles (reruns)."""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..")))

from core.execution.scheduled_runner import select_items  # noqa: E402

QUEUE = [
    {"id": "t1|rate_limit|early", "kind": "block", "position": 0,
     "goals": [{"policy": "FR_cold"}, {"policy": "SR_warm"}]},
    {"id": "t1|B0|1", "kind": "b0", "position": 1, "goals": [{"policy": "B0"}]},
]


def _file(tmp_path, text):
    p = tmp_path / "items.tsv"
    p.write_text(text)
    return str(p)


def test_whole_item_and_single_policy(tmp_path):
    q = select_items(QUEUE, _file(tmp_path, "# c\nt1|B0|1\nt1|rate_limit|early\tSR_warm\n"))
    assert [i["id"] for i in q] == ["t1|rate_limit|early", "t1|B0|1"]
    assert [g["policy"] for g in q[0]["goals"]] == ["SR_warm"]
    assert QUEUE[0]["goals"][0]["policy"] == "FR_cold"      # original queue untouched


def test_unknown_item_stops(tmp_path):
    with pytest.raises(SystemExit):
        select_items(QUEUE, _file(tmp_path, "t9|timeout|late\n"))
