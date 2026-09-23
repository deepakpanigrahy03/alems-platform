# New enums introduced by Chunk 39 design.
# Not yet consumed by runtime code in phase 39.1 (SPEC_39_1 section 3 rule 3).
# Compliance: PAC-3 extension and MPC-1 vocabulary are the authority for Fidelity names.
from enum import Enum


class Fidelity(str, Enum):
    """Value-level fidelity for measurements and derived quantities."""
    # Direct hardware or OS read, no math (PAC-3 MEASURED).
    MEASURED = "MEASURED"
    # Computed from measured inputs (MPC-1 CALCULATED).
    CALCULATED = "CALCULATED"
    # Uses external constants, ML models, or structural inference;
    # estimators must also emit an error bound (PAC-3 INFERRED, D9.3).
    INFERRED = "INFERRED"
    # Hardware unavailable; value is zero or absent (PAC-3 LIMITED).
    LIMITED = "LIMITED"


class CoverageState(str, Enum):
    """Per-table or per-column coverage in a run's coverage manifest (D12.3)."""
    FILLED = "filled"
    EMPTY = "empty"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"


class IsolationLevel(str, Enum):
    """How much of the machine a run exclusively owns (D10.5)."""
    EXCLUSIVE = "exclusive"
    PARTITIONED = "partitioned"
    SHARED = "shared"


class ExtensionFamily(str, Enum):
    """Plugin family classification (D3.2a)."""
    EXECUTION = "execution"
    MEASUREMENT = "measurement"
    SEMANTIC = "semantic"
    PERSISTENCE = "persistence"
    OUTPUT = "output"
