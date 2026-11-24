"""
alems_plugin_pravaha.kernel_characterize: one Nsight Compute characterisation pass per
kernel_rw condition of a finished sequencer session.

Author: Deepak Panigrahy

Run after all measured brackets (so profiling never disturbs an idle baseline):
    alems sequence --kernel-pass SESSION_ID

Per distinct condition (placement, op, prefetch, init, size), the matched normal run is the
first completed, non control treatment run in planned order (declared rule
`first_completed_treatment`). The pass runs known_kernel with a short TIMES under
Nsight Compute (45.3c, kernel_traffic_ncu_v1). Launch counts differ from the treatment, so
the whole run agreement is scope_mismatch with a NULL ratio by design; the pass reports per
launch read and write sectors, the read kernel's auxiliary writes and setup kernels.
Both pass counts are stored in the workload identity.

Configuration: ALEMS_KRW_PROFILE_TIMES passes in the profiled run (default 2).
"""

import json
import os
import sys
from typing import Any, Dict, List

from . import kernel_pass

MATCH_RULE = "first_completed_treatment"


def treatment_runs(conn, session_id):
    # type: (Any, int) -> List[Dict[str, Any]]
    """First completed non control kernel_rw treatment run per condition, in planned order."""
    rows = conn.execute(
        "SELECT s.params_json, s.condition, ar.run_id, s.planned_index, "
        "  (SELECT value FROM energy_validation_seq_step_results r "
        "   WHERE r.attempt_id = a.attempt_id AND r.key = 'times') "
        "FROM energy_validation_seq_steps s "
        "JOIN energy_validation_seq_attempts a ON a.step_id = s.step_id AND a.execution_status = 'completed' "
        "JOIN energy_validation_seq_attempt_runs ar ON ar.attempt_id = a.attempt_id "
        "WHERE s.session_id = ? AND s.kind = 'kernel_rw' AND s.role = 'treatment' "
        "ORDER BY s.planned_index, ar.child_seq", (session_id,)).fetchall()
    seen, out = set(), []
    for params_json, condition, run_id, idx, times in rows:
        p = json.loads(params_json)
        if p.get("control"):
            continue
        key = (p["placement"], p["op"], p.get("prefetch"), p.get("init"), p["size_mib"])
        if key in seen:
            continue
        seen.add(key)
        out.append({"params": p, "condition": condition, "run_id": run_id, "treatment_times": times})
    return out


def command(params, times):
    # type: (Dict[str, Any], int) -> List[str]
    cmd = [sys.executable, "-m", "pravaha_mem.known_kernel", params["placement"], params["op"],
           str(times), str(int(params["size_mib"])), "--init", params.get("init") or os.environ.get("ALEMS_KRW_INIT") or "gpu"]
    if params["placement"] == "managed":
        cmd += ["--prefetch", params["prefetch"]]
    return cmd


def characterize_session(conn, session_id):
    # type: (Any, int) -> List[Dict[str, Any]]
    """One stored pass per condition; returns the pass rows."""
    times = int(os.environ.get("ALEMS_KRW_PROFILE_TIMES") or 2)
    out = []
    for t in treatment_runs(conn, session_id):
        phase = json.dumps({"condition": t["condition"], "match_rule": MATCH_RULE,
                            "treatment_times": t["treatment_times"], "profile_times": times}, sort_keys=True)
        row = kernel_pass.profile(conn, command(t["params"], times), {"scope_subset": True},
                                  matched_run_id=t["run_id"], interpreter=sys.executable, phase=phase)
        out.append(row)
    return out
