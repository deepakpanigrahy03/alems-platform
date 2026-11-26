"""
Span vocabulary of an experiment recording (id thermal_session, version 1).

Registered in the alems.spans.vocabularies entry point group. It labels the
parts of one continuous recording on the recorder's time line:

    session
      idle_window   pre-window before the first run (purpose pre), or a
                    planned idle period after the workload (purpose post)
      warmup        planned warm-up before the workload
      workload      first run start to last run end
        run_slot    one harness run (pause to resume of the recorder)
        gap         unplanned wait between two runs (resume to next pause)
        goal_slot   all attempts of one goal, retries included
        pause       planned wait between goals, with its pacing rule
        calibration_step  one held load level
      cooling_tail  last run end to the end of the recording

The top-level kinds tile the session with no gap and no overlap. Inside
the workload, run slots and gaps tile it the same way. warmup, the post
idle window, goal_slot, pause and calibration_step are written by the
session runner; the recorder writes the other kinds.
"""
from __future__ import annotations

from alems_sdk.spans import AttrSpec as A, KindSpec as K, SpanVocabulary

ALEMS_PLUGIN_META = {
    "plugin_id":           "thermal_session",
    "extension_point":     "alems.spans.vocabularies",
    "family":              "semantic",
    "version":             "1.0.0",
    "sdk_range":           ">=0.1.0,<2.0.0",
    "description":         "Experiment recording span vocabulary: session parts on one time line",
    "supported_platforms": ["linux"],
    "required_privileges": [],
    "capabilities":        [],
}

_TOP = frozenset({"session"})
_IN_WORKLOAD = frozenset({"workload"})

THERMAL_SESSION = SpanVocabulary(
    vocabulary_id="thermal_session",
    version="1",
    kinds={
        "session": K(frozenset({None}), {
            "session_id":      A("int", True),       # te_session.session_id
            "profile_id":      A("string", True),
            "pacing_plugin":   A("string", True),
            "machine_profile": A("string", True),
        }),
        "idle_window": K(_TOP, {
            "purpose":   A("string", True, ("pre", "post")),
            "planned_s": A("float", True),
            "actual_s":  A("float", True),
        }),
        "warmup": K(_TOP, {
            "rule":    A("string", True),
            "seconds": A("float", True),
        }),
        "workload": K(_TOP, {
            "runs": A("int", True),                    # harness runs inside
        }),
        "cooling_tail": K(_TOP, {
            # cool-down outcome; not_requested = no cool-down was asked for,
            # the tail is only the time between the last run and the close
            "status":      A("string", True,
                             ("completed", "timed_out", "aborted", "not_requested")),
            "reached":     A("bool", True),
            "seconds":     A("float", True),
            "tolerance_c": A("float"),                 # above pre-window median
        }),
        "run_slot": K(_IN_WORKLOAD, {
            "slot_index": A("int", True),              # 1-based order of the run
            "run_match":  A("string", True, ("matched", "none", "ambiguous", "pending")),
            "run_id":     A("int"),                    # NULL unless run_match = matched
        }),
        "gap": K(_IN_WORKLOAD, {
            "seconds": A("float", True),
        }),
        "goal_slot": K(_IN_WORKLOAD, {
            "goal_id":         A("string", True),
            "workflow":        A("string", True),
            "attempt_run_ids": A("json", True),        # list, in attempt order
            "success":         A("bool", True),
            "agent_attempt":   A("string", False, ("run", "not_run")),
        }),
        "pause": K(_IN_WORKLOAD, {
            "planned_s": A("float", True),
            "actual_s":  A("float", True),
            "rule":      A("string", True),
        }),
        "calibration_step": K(_IN_WORKLOAD, {
            "channel": A("string", True),
            "level":   A("float", True),
            "hold_s":  A("float", True),
            "hold_achieved": A("bool", True),
            "settled": A("bool", False),     # set by the calibration analysis, not by the plan
        }),
    },
)
