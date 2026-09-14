# Extension identity metadata.
# Read by the plugin discovery system at startup.
# Configuration schema is declared via get_config_schema() on the class,
# not here — this dict is identity only.

ALEMS_PLUGIN_META = {
    "name": "output_quality",
    "version": "1.0.0",
    "alems_compat": ">=1.0,<2.0",
    "description": (
        "LLM-as-judge quality scoring extension for A-LEMS. "
        "Records quality scores alongside inference energy measurements "
        "to enable qEpG (quality Energy per Goal) analysis."
    ),
    "platform_constraint": None,  # works on all platforms
}
