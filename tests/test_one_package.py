"""
tests/test_one_package.py — automated one-package test (R7.8 SPEC_39_3).

Verifies that:
1. The three fixture packages are discoverable via entry points.
2. Each passes the conformance kit.
3. core/, migrations/schema/, and gui/ are byte-identical to baseline
   (hashes recorded at test start, compared at end).

Run only when fixture packages are installed (marked with the
one_package marker; skipped otherwise).

Fixtures are installed by:
    venv/bin/pip install -e tests/fixtures/one_package/alems-fixture-reader
    venv/bin/pip install -e tests/fixtures/one_package/alems-fixture-estimator
    venv/bin/pip install -e tests/fixtures/one_package/alems-fixture-model
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import sys
from importlib.metadata import entry_points
from typing import Dict

import pytest

from alems_sdk.conformance import run_conformance

# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _dir_hash(root: pathlib.Path) -> str:
    """
    Compute a stable hash of all .py files under root.

    Excludes __pycache__ directories. Used to detect accidental modification
    of core, migrations, or gui during the one-package test (INV-18).

    Args:
        root: Directory to hash recursively.

    Returns:
        Hex digest string.
    """
    h = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        h.update(path.read_bytes())
    return h.hexdigest()


def _get_entry_points(group: str) -> Dict[str, object]:
    """
    Load entry points for group.

    Returns dict of name -> loaded class. Skips entries that fail to load
    and records them as warnings rather than raising.
    """
    try:
        eps = entry_points(group=group)
    except TypeError:
        # Python 3.9 compat: entry_points() takes no keyword args in some builds.
        eps = entry_points().get(group, [])

    result = {}
    for ep in eps:
        try:
            result[ep.name] = ep.load()
        except Exception as exc:
            # Log but do not fail — the test asserts specific names below.
            print("WARNING: could not load %s from %s: %s" % (ep.name, group, exc), file=sys.stderr)
    return result


# ---------------------------------------------------------------------------
# Skip condition: fixture packages must be installed.
# ---------------------------------------------------------------------------

def _fixture_installed(name: str) -> bool:
    """Check if a fixture package is installed."""
    try:
        from importlib.metadata import packages_distributions
        dist_map = packages_distributions()
        return any(name in v for v in dist_map.values())
    except Exception:
        return False


_FIXTURES_PRESENT = (
    _fixture_installed("fixture_reader")
    and _fixture_installed("fixture_estimator")
    and _fixture_installed("fixture_model")
)

pytestmark = pytest.mark.skipif(
    not _FIXTURES_PRESENT,
    reason="fixture packages not installed; run pip install -e on all three fixtures first",
)

# ---------------------------------------------------------------------------
# Baseline hash captured once at module load.
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).parent.parent
_BASELINE_HASHES: Dict[str, str] = {}

def _capture_baseline() -> None:
    """Capture hashes of core, migrations/schema, and gui directories."""
    for subdir in ["core", "migrations/schema", "gui"]:
        path = _REPO_ROOT / subdir
        if path.exists():
            _BASELINE_HASHES[subdir] = _dir_hash(path)

_capture_baseline()


# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------

class TestOnePackage:

    def test_fixture_reader_discoverable(self):
        """fixture_constant must appear in alems.readers.energy entry points."""
        loaded = _get_entry_points("alems.readers.energy")
        assert "fixture_constant" in loaded, (
            "fixture_constant not found in alems.readers.energy; "
            "did you pip install -e the fixture_reader package?"
        )

    def test_fixture_estimator_discoverable(self):
        """fixture_estimator must appear in alems.readers.energy entry points."""
        loaded = _get_entry_points("alems.readers.energy")
        assert "fixture_estimator" in loaded, (
            "fixture_estimator not found in alems.readers.energy"
        )

    def test_fixture_model_discoverable(self):
        """fixture_model must appear in alems.models.fragments entry points."""
        loaded = _get_entry_points("alems.models.fragments")
        assert "fixture_model" in loaded, (
            "fixture_model not found in alems.models.fragments"
        )

    def test_fixture_reader_conformance_passes(self):
        """ConstantEnergyReader must pass the measurement conformance kit."""
        loaded = _get_entry_points("alems.readers.energy")
        cls = loaded["fixture_constant"]
        meta = getattr(cls, "ALEMS_PLUGIN_META", {})
        report = run_conformance("fixture_constant", meta, cls=cls, origin="external")
        assert report.passed, (
            "fixture_constant failed conformance: %s"
            % [f for r in report.results for f in r.failures]
        )

    def test_fixture_estimator_conformance_passes(self):
        """FixtureEstimator must pass the measurement conformance kit."""
        loaded = _get_entry_points("alems.readers.energy")
        cls = loaded["fixture_estimator"]
        meta = getattr(cls, "ALEMS_PLUGIN_META", {})
        report = run_conformance("fixture_estimator", meta, cls=cls, origin="external")
        assert report.passed, (
            "fixture_estimator failed conformance: %s"
            % [f for r in report.results for f in r.failures]
        )

    def test_fixture_model_conformance_passes(self):
        """FixtureModelFragment must pass the execution conformance kit."""
        loaded = _get_entry_points("alems.models.fragments")
        cls = loaded["fixture_model"]
        meta = getattr(cls, "ALEMS_PLUGIN_META", {})
        report = run_conformance("fixture_model", meta, cls=cls, origin="external")
        assert report.passed, (
            "fixture_model failed conformance: %s"
            % [f for r in report.results for f in r.failures]
        )

    def test_core_not_modified(self):
        """core/ must be byte-identical to baseline (INV-18)."""
        if "core" not in _BASELINE_HASHES:
            pytest.skip("core/ directory not found at repo root")
        current = _dir_hash(_REPO_ROOT / "core")
        assert current == _BASELINE_HASHES["core"], (
            "core/ was modified during the one-package test — INV-18 violation"
        )

    def test_migrations_schema_not_modified(self):
        """migrations/schema/ must be byte-identical to baseline (INV-18)."""
        if "migrations/schema" not in _BASELINE_HASHES:
            pytest.skip("migrations/schema/ not found at repo root")
        current = _dir_hash(_REPO_ROOT / "migrations/schema")
        assert current == _BASELINE_HASHES["migrations/schema"], (
            "migrations/schema/ was modified during the one-package test — INV-18 violation"
        )

    def test_gui_not_modified(self):
        """gui/ must be byte-identical to baseline (INV-18)."""
        if "gui" not in _BASELINE_HASHES:
            pytest.skip("gui/ directory not found at repo root")
        current = _dir_hash(_REPO_ROOT / "gui")
        assert current == _BASELINE_HASHES["gui"], (
            "gui/ was modified during the one-package test — INV-18 violation"
        )
