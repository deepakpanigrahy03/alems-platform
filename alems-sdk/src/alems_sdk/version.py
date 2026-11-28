# SDK version axis (SPEC_39_1 section 3, D4.1).
# Semantic version; Gate F freezes the contract with its own bump.
SDK_VERSION: str = "1.1.0"   # must equal alems-sdk/pyproject.toml; 1.1.0: thermal sensor catalog

# Runtime versions compatible with this SDK major.
# Plugin manifests declare sdk_range; runtime refuses plugins outside this range (D4.2).
SUPPORTED_RUNTIME_RANGE: str = ">=1.0.0,<2.0.0"
