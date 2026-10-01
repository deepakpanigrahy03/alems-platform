"""
Compatibility path (39.5.1a.2): the contract now lives in alems_sdk.energy_reading (D2.1).

Every name of the SDK module, private ones included, is bound here as the
identical object, so existing imports, isinstance checks and registry keys are
unchanged (D2.2, Rule S). New code imports from alems_sdk.energy_reading.
"""

import alems_sdk.energy_reading as _sdk_module

# Bind every attribute of the SDK module except dunder metadata, so names this
# shim does not list explicitly (constants, helpers) keep resolving.
globals().update(
    {k: v for k, v in vars(_sdk_module).items() if not k.startswith("__")}
)
