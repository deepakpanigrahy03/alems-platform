# SDK version axis (SPEC_39_1 section 3, D4.1).
# 0.9.0 until Gate F; Gate F bumps to 1.0.0 and freezes the contract.
SDK_VERSION: str = "0.9.0"

# Runtime versions compatible with this SDK major.
# Plugin manifests declare sdk_range; runtime refuses plugins outside this range (D4.2).
SUPPORTED_RUNTIME_RANGE: str = ">=1.0.0,<2.0.0"
