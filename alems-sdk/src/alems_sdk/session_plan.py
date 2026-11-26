"""
alems_sdk.session_plan: contract for session plans run by run_experiment.

A session plan decides, step by step, what an experiment session does next.
run_experiment owns execution, persistence, the safety guard and the
continuous recording; a plan only chooses the next step and reads outcomes.
Plans register under the entry point group alems.harness.pacing and import
only alems_sdk.

Step kinds (generic; no study-specific meaning in core):
  goal        run one goal of a task with one workflow
  load_step   run goals of a task with one workflow until hold_s has passed
  wait        planned pause of a given length, with a label
  done        end the session

After every step the plan receives an Outcome with what really happened
(success, ids, times, and for waits the planned and actual length).
Zero imports from core or scripts (INV-14).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

STEP_KINDS = ("goal", "load_step", "wait", "done")
WORKFLOWS = ("linear", "agentic")


@dataclass(frozen=True)
class Step:
    """One requested step; build it with goal(), load_step(), wait() or done()."""

    kind: str
    task_id: Optional[str] = None
    workflow: Optional[str] = None
    provider: Optional[str] = None      # None: the session's first provider
    hold_s: Optional[float] = None
    seconds: Optional[float] = None
    label: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)   # plan-defined, echoed in the outcome


def goal(task_id, workflow, provider=None, label=None, meta=None):
    # type: (str, str, Optional[str], Optional[str], Optional[Dict]) -> Step
    """Run one goal of task_id with workflow (linear or agentic)."""
    return Step("goal", task_id=task_id, workflow=workflow, provider=provider, label=label, meta=meta or {})


def load_step(task_id, workflow, hold_s, provider=None, label=None, meta=None):
    # type: (str, str, float, Optional[str], Optional[str], Optional[Dict]) -> Step
    """Run goals of task_id with workflow until hold_s seconds have passed."""
    return Step("load_step", task_id=task_id, workflow=workflow, provider=provider, hold_s=float(hold_s),
                label=label, meta=meta or {})


def wait(seconds, label, meta=None):
    # type: (float, str, Optional[Dict]) -> Step
    """Planned pause; the safety guard may add its own wait before the next goal (recorded apart)."""
    return Step("wait", seconds=float(seconds), label=label, meta=meta or {})


def done(label=None):
    # type: (Optional[str]) -> Step
    """End the session."""
    return Step("done", label=label)


@dataclass
class Outcome:
    """What really happened for one step; missing values are None, never 0."""

    step: Step
    status: str                          # completed | failed | error
    success: Optional[bool] = None       # goal and load_step: all goals solved
    goal_ids: List[int] = field(default_factory=list)
    attempt_ids: List[int] = field(default_factory=list)
    run_ids: List[int] = field(default_factory=list)
    step_start_utc_ns: Optional[int] = None    # when the step was requested (before any guard wait)
    first_run_start_ns: Optional[int] = None   # start of the first measured run of the step
    last_run_end_ns: Optional[int] = None      # end of the last measured run of the step
    end_utc_ns: Optional[int] = None
    planned_s: Optional[float] = None          # wait: requested length; load_step: hold_s
    actual_s: Optional[float] = None           # wait: slept length; load_step: elapsed
    hold_achieved: Optional[bool] = None
    failure_reason: Optional[str] = None


class SessionPlanABC(ABC):
    """
    Base class of a session plan.

    PARAMS declares accepted parameters, checked before the session starts:
        {"gap_factor": {"type": "number", "required": True}}
    """

    NAME = ""
    PARAMS = {}  # type: Dict[str, Dict[str, Any]]
    ALEMS_PLUGIN_META = {}  # type: Dict[str, Any]

    @classmethod
    def validate(cls, params):
        # type: (Dict[str, Any]) -> List[str]
        """Errors in the plan parameters (empty when valid); never raises."""
        errors = []  # type: List[str]
        for name, spec in cls.PARAMS.items():
            errors.extend(cls._param_errors(name, spec, params))
        return errors

    @classmethod
    def _param_errors(cls, name, spec, params):
        # type: (str, Dict[str, Any], Dict[str, Any]) -> List[str]
        """Errors of one declared parameter."""
        if name not in params:
            return ["%s: missing parameter %s" % (cls.NAME, name)] if spec.get("required") else []
        value, out = params[name], []
        # bool is an int in Python; never accept true as a number
        if spec.get("type") == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
            out.append("%s: %s must be a number" % (cls.NAME, name))
        if spec.get("choices") and value not in spec["choices"]:
            out.append("%s: %s=%r not in %s" % (cls.NAME, name, value, spec["choices"]))
        return out

    @abstractmethod
    def start(self, params, context):
        # type: (Dict[str, Any], Dict[str, Any]) -> None
        """
        Called once before the first step. context holds tasks (ids), providers,
        recorder (the open continuous recording, or None) and config_path (the experiment
        config file, or None); the plan may refuse by raising ValueError (for example a
        computed pause below zero).
        """

    @abstractmethod
    def next_step(self, history):
        # type: (List[Outcome]) -> Step
        """The next step, given every outcome so far (oldest first)."""
