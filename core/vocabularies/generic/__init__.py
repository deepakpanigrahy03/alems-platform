"""Generic span vocabulary: job, task and function spans (id generic, version 1)."""
# core/vocabularies/generic/__init__.py
# Generic span vocabulary (alems.spans.vocabularies entry point, id=generic, version=1).
# Kinds: job, task, function.
# No projections onto existing tables.
# Used by embedded SDK (39.6) and observer (39.7) for non-agent workloads.

ALEMS_PLUGIN_META = {
    "plugin_id":           "generic",
    "extension_point":     "alems.spans.vocabularies",
    "family":              "semantic",
    "version":             "1.0.0",
    "sdk_range":           ">=0.1.0,<2.0.0",
    "description":         "Generic span vocabulary: job, task, function",
    "supported_platforms": ["linux", "darwin", "windows"],
    "required_privileges": [],
    "capabilities":        [],
}

GENERIC_KINDS = {"job", "task", "function"}
