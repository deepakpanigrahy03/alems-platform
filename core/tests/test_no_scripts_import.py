"""
test_no_scripts_import.py -- Enforces INV-14 variant: core must not import scripts.

Known exceptions recorded here are hardware-specific conditional imports
that run only on GN100 (SPBM, DCGM).  They are backlog item B39-4b-2.
Remove from KNOWN_EXCEPTIONS when moved to core.attribution.legacy_v1.
"""
import os
import re
import pytest

# Root of the core package relative to repo root.
CORE_ROOT = os.path.join(os.path.dirname(__file__), "..", "core")

# Lines matching this pattern are violations.
IMPORT_PATTERN = re.compile(r"^\s*(from scripts|import scripts)")

# Known exceptions: (relative_file_path, line_substring).
# A line is exempt only when BOTH file suffix and substring match.
KNOWN_EXCEPTIONS = [
    # Hardware ETL: GN100 SPBM telemetry, conditional import inside function.
    ("experiment_runner.py", "gpu_spbm_etl"),
    ("experiment_runner.py", "spbm_telemetry_etl"),
    ("experiment_runner.py", "network_energy_etl"),
    ("goal_execution_manager.py", "network_energy_etl"),
    # path_loader: not ETL, utility used for DB path resolution.
    # Backlog B39-4b-3: move get_alems_db_path into core.
    ("experiment_runner.py", "path_loader"),
    ("goal_execution_manager.py", "path_loader"),
    ("goal_execution_manager.py", "get_alems_db_path"),
]


def _is_known_exception(filepath, line):
    # type: (str, str) -> bool
    """Return True if this file+line combination is a declared exception."""
    for file_suffix, line_fragment in KNOWN_EXCEPTIONS:
        if filepath.endswith(file_suffix) and line_fragment in line:
            return True
    return False


def _collect_violations():
    # type: () -> list
    violations = []
    for dirpath, _dirs, files in os.walk(CORE_ROOT):
        for fname in files:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(dirpath, fname)
            with open(fpath, encoding="utf-8", errors="replace") as fh:
                for lineno, line in enumerate(fh, 1):
                    if IMPORT_PATTERN.match(line):
                        if not _is_known_exception(fpath, line):
                            violations.append((fpath, lineno, line.rstrip()))
    return violations


def test_core_does_not_import_scripts():
    """
    Core must never import from scripts (INV-14 extension, CH39-3).

    Violations indicate ETL that has not been moved to core.attribution
    or a new dependency on scripts utilities.  Add to KNOWN_EXCEPTIONS
    only with a backlog item and a chunk assignment.
    """
    violations = _collect_violations()
    if violations:
        lines = "\n".join(f"  {f}:{n}  {l}" for f, n, l in violations)
        pytest.fail(
            f"core imports scripts in {len(violations)} place(s):\n{lines}\n\n"
            "Move the ETL to core.attribution.legacy_v1 or add a named "
            "exception to KNOWN_EXCEPTIONS with a backlog item."
        )
