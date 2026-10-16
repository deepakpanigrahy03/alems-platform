"""RAF R6: TABLE@arm options resolve per block from schedule.tables (E3 token and oracle arms)."""

import pytest

from core.execution.schedule import build_blocks


def _cfg(tables):
    return {
        "seeds": {"block_order": 1, "policy_order": 2, "model_seed": 0},
        "early_ordinal": 2,
        "families": {"clean": {"classes": {"timeout": "tool"},
                               "options": ["SR_warm@ctrl", "FR_warm", "TABLE@oracle",
                                           "TABLE@token"],
                               "points": {"early": 1, "late": 1}}},
        "tasks": {"bfcl_t1": {"stratum": "short", "families": ["clean"],
                              "targets": {"clean": {"late": 4}}}},
        "tables": tables,
    }


TABLES = {"oracle": {"bfcl_t1|timeout|early": "FR_cold", "bfcl_t1|timeout|late": "SG_warm"},
          "token": {"bfcl_t1|timeout|early": "SR_warm", "bfcl_t1|timeout|late": "RB_cold"}}


def test_table_options_resolve_per_cell():
    blocks = {b["id"]: b for b in build_blocks(_cfg(TABLES))}
    late = {g["arm"]: g for g in blocks["bfcl_t1|timeout|late"]["goals"]}
    early = {g["arm"]: g for g in blocks["bfcl_t1|timeout|early"]["goals"]}
    assert late["oracle"]["policy"] == "SG_warm" and late["oracle"]["table"] == "oracle"
    assert early["token"]["policy"] == "SR_warm"
    assert late["ctrl"]["policy"] == "SR_warm" and "table" not in late["ctrl"]


def test_all_arms_of_a_cell_in_one_block():
    for b in build_blocks(_cfg(TABLES)):
        assert sorted(g["arm"] for g in b["goals"]) == ["ctrl", "fixed", "oracle", "token"]


def test_missing_cell_stops_queue_building():
    bad = {"oracle": {"bfcl_t1|timeout|early": "FR_cold"}, "token": TABLES["token"]}
    with pytest.raises(ValueError):
        build_blocks(_cfg(bad))


def test_no_tables_keeps_old_behaviour():
    cfg = _cfg({})
    cfg["families"]["clean"]["options"] = ["SR_warm", "FR_warm"]
    pols = sorted(g["policy"] for b in build_blocks(cfg) for g in b["goals"])
    assert pols == ["FR_warm", "FR_warm", "SR_warm", "SR_warm"]
