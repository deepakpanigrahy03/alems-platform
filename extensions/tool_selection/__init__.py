# Extension identity metadata.
# Read by the plugin discovery system at startup.
# Configuration schema is declared via get_config_schema() on the class,
# not here — this dict is identity only.
#
# SPEC 35I: this extension ships as an EXTERNAL plugin (unlike
# output_quality, which is built-in), same package as
# RetrievalToolSelector (alems-selector-retrieval) — the design v2.1
# Section 6 decision, adopted because the embedding dependency this
# extension exists to support shouldn't be forced onto every core
# install.

ALEMS_PLUGIN_META = {
    "name": "tool_selection",
    "version": "1.0.0",
    "alems_compat": ">=1.0,<2.0",
    "description": (
        "Records retrieval-based tool selection energy and metadata "
        "alongside inference energy measurements. Table-owning "
        "counterpart to RetrievalToolSelector (SPEC 35I) — the "
        "selector itself writes directly to this extension's table, "
        "since selection happens before the on_post_run() lifecycle "
        "point (see extension.py's on_post_run() docstring)."
    ),
    "platform_constraint": None,
}
