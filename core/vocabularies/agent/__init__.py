# core/vocabularies/agent/__init__.py
# Agent vocabulary (alems.spans.vocabularies entry point, id=agent, version=1).
# Declares the span kinds used by the A-LEMS runner and provides projection helpers
# that write existing tables (goal_execution, goal_attempt, etc.) from span context.
# Projection SQL is moved verbatim from goal_tracker.py and experiment_runner.py;
# commit points are unchanged.

from .vocabulary import AgentVocabulary
from .projections import AgentProjections

__all__ = ["AgentVocabulary", "AgentProjections"]

# Registration metadata read by the conformance kit and registry service.
ALEMS_PLUGIN_META = {
    "plugin_id":          "agent",
    "extension_point":    "alems.spans.vocabularies",
    "family":             "semantic",
    "version":            "1.0.0",
    "sdk_range":          ">=0.1.0,<2.0.0",
    "description":        "Agent span vocabulary: experiment, run, goal, attempt, turn, llm_call, tool_call, phase, retry, recovery",
    "supported_platforms": ["linux", "darwin", "windows"],
    "required_privileges": [],
    "capabilities":       ["projections"],
}
