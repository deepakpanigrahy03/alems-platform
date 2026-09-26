"""
tests/test_conformance.py — unit tests for alems_sdk conformance kits.

Tests per-kit logic using synthetic stub classes. Does not touch the
real DB or run experiments. Rule S: no new measurement, no schema changes.
"""
from __future__ import annotations

import pytest

from alems_sdk.conformance import run_conformance, ConformanceReport
from alems_sdk._kit_measurement import MeasurementKit
from alems_sdk._kit_execution import ExecutionKit
from alems_sdk._kit_persistence import PersistenceKit, OutputKit


# ---------------------------------------------------------------------------
# Stub classes used across tests.
# ---------------------------------------------------------------------------

class GoodReader:
    """Minimal compliant energy reader stub."""
    ALEMS_PLUGIN_META = {
        "plugin_id": "good_reader",
        "family": "measurement",
        "extension_point": "alems.readers.energy",
        "version": "0.1.0",
    }
    FIDELITY = "MEASURED"
    ERROR_BOUND = None

    @classmethod
    def is_available(cls):
        return True

    @classmethod
    def get_name(cls):
        return "good_reader"

    @classmethod
    def get_config_schema(cls):
        return {"type": "object", "properties": {}}


class MissingFidelityReader:
    """Reader missing FIDELITY attribute."""
    ALEMS_PLUGIN_META = {
        "plugin_id": "no_fidelity",
        "family": "measurement",
        "extension_point": "alems.readers.energy",
        "version": "0.1.0",
    }

    @classmethod
    def is_available(cls):
        return True

    @classmethod
    def get_name(cls):
        return "no_fidelity"


class InferredNoErrorBound:
    """Estimator with FIDELITY=INFERRED but no ERROR_BOUND — should fail."""
    ALEMS_PLUGIN_META = {
        "plugin_id": "bad_estimator",
        "family": "measurement",
        "extension_point": "alems.readers.energy",
        "version": "0.1.0",
    }
    FIDELITY = "INFERRED"
    # ERROR_BOUND intentionally missing

    @classmethod
    def is_available(cls):
        return True

    @classmethod
    def get_name(cls):
        return "bad_estimator"

    @classmethod
    def get_config_schema(cls):
        return {"type": "object"}


class GoodEstimator:
    """Compliant estimator with FIDELITY=INFERRED and ERROR_BOUND."""
    ALEMS_PLUGIN_META = {
        "plugin_id": "good_estimator",
        "family": "measurement",
        "extension_point": "alems.readers.energy",
        "version": "0.1.0",
    }
    FIDELITY = "INFERRED"
    ERROR_BOUND = "±30%"

    @classmethod
    def is_available(cls):
        return True

    @classmethod
    def get_name(cls):
        return "good_estimator"

    @classmethod
    def get_config_schema(cls):
        return {"type": "object"}


class GoodOutput:
    """Compliant output plugin stub."""
    ALEMS_PLUGIN_META = {
        "plugin_id": "good_output",
        "family": "output",
        "extension_point": "alems.outputs",
        "version": "0.1.0",
    }

    @classmethod
    def get_config_schema(cls):
        return {"type": "object"}

    def export(self, store, dest):
        pass


class BadOutput:
    """Output plugin missing export()."""
    ALEMS_PLUGIN_META = {
        "plugin_id": "bad_output",
        "family": "output",
        "extension_point": "alems.outputs",
        "version": "0.1.0",
    }


class GoodExtension:
    """Compliant persistence/extension plugin."""
    ALEMS_PLUGIN_META = {
        "plugin_id": "good_ext",
        "family": "persistence",
        "extension_point": "alems.extensions",
        "namespace": "test_ns",
        "version": "0.1.0",
    }
    NAMESPACE = "test_ns"

    @classmethod
    def get_config_schema(cls):
        return {"type": "object"}


class BadExtensionNoNamespace:
    """Extension plugin missing namespace."""
    ALEMS_PLUGIN_META = {
        "plugin_id": "bad_ext",
        "family": "persistence",
        "extension_point": "alems.extensions",
        "version": "0.1.0",
    }


# ---------------------------------------------------------------------------
# MeasurementKit tests.
# ---------------------------------------------------------------------------

class TestMeasurementKit:

    def test_good_reader_passes(self):
        kit = MeasurementKit()
        kit.check(GoodReader, GoodReader.ALEMS_PLUGIN_META, "first_party")
        assert kit.passed
        assert kit.failures == []

    def test_missing_fidelity_first_party_fails(self):
        kit = MeasurementKit()
        kit.check(MissingFidelityReader, MissingFidelityReader.ALEMS_PLUGIN_META, "first_party")
        assert not kit.passed
        assert any("FIDELITY" in f for f in kit.failures)

    def test_missing_fidelity_external_is_warning(self):
        kit = MeasurementKit()
        kit.check(MissingFidelityReader, MissingFidelityReader.ALEMS_PLUGIN_META, "external")
        # External: no FIDELITY is a warning, not a failure.
        assert kit.passed
        assert any("FIDELITY" in w for w in kit.warnings)

    def test_inferred_no_error_bound_fails(self):
        kit = MeasurementKit()
        kit.check(InferredNoErrorBound, InferredNoErrorBound.ALEMS_PLUGIN_META, "first_party")
        assert not kit.passed
        assert any("ERROR_BOUND" in f for f in kit.failures)

    def test_good_estimator_passes(self):
        kit = MeasurementKit()
        kit.check(GoodEstimator, GoodEstimator.ALEMS_PLUGIN_META, "first_party")
        assert kit.passed

    def test_missing_is_available_fails(self):
        class NoIsAvailable:
            ALEMS_PLUGIN_META = {
                "plugin_id": "x", "family": "measurement",
                "extension_point": "alems.readers.energy", "version": "0.1.0",
            }
            FIDELITY = "MEASURED"
            @classmethod
            def get_name(cls): return "x"
            @classmethod
            def get_config_schema(cls): return {}

        kit = MeasurementKit()
        kit.check(NoIsAvailable, NoIsAvailable.ALEMS_PLUGIN_META, "first_party")
        assert not kit.passed
        assert any("is_available" in f for f in kit.failures)

    def test_invalid_fidelity_value_fails(self):
        class BadFidelity:
            ALEMS_PLUGIN_META = {
                "plugin_id": "x", "family": "measurement",
                "extension_point": "alems.readers.energy", "version": "0.1.0",
            }
            FIDELITY = "DERIVED"  # not in compliance vocabulary
            ERROR_BOUND = None
            @classmethod
            def is_available(cls): return True
            @classmethod
            def get_name(cls): return "x"
            @classmethod
            def get_config_schema(cls): return {}

        kit = MeasurementKit()
        kit.check(BadFidelity, BadFidelity.ALEMS_PLUGIN_META, "first_party")
        assert not kit.passed
        assert any("compliance vocabulary" in f for f in kit.failures)


# ---------------------------------------------------------------------------
# OutputKit tests.
# ---------------------------------------------------------------------------

class TestOutputKit:

    def test_good_output_passes(self):
        kit = OutputKit()
        kit.check(GoodOutput, GoodOutput.ALEMS_PLUGIN_META, "first_party")
        assert kit.passed

    def test_missing_export_fails(self):
        kit = OutputKit()
        kit.check(BadOutput, BadOutput.ALEMS_PLUGIN_META, "first_party")
        assert not kit.passed
        assert any("export" in f for f in kit.failures)


# ---------------------------------------------------------------------------
# PersistenceKit tests.
# ---------------------------------------------------------------------------

class TestPersistenceKit:

    def test_good_extension_passes(self):
        kit = PersistenceKit()
        kit.check(GoodExtension, GoodExtension.ALEMS_PLUGIN_META, "first_party")
        assert kit.passed

    def test_missing_namespace_fails(self):
        kit = PersistenceKit()
        kit.check(BadExtensionNoNamespace, BadExtensionNoNamespace.ALEMS_PLUGIN_META, "first_party")
        assert not kit.passed
        assert any("namespace" in f for f in kit.failures)


# ---------------------------------------------------------------------------
# run_conformance integration tests.
# ---------------------------------------------------------------------------

class TestRunConformance:

    def test_good_reader_report_passes(self):
        report = run_conformance(
            "good_reader",
            GoodReader.ALEMS_PLUGIN_META,
            cls=GoodReader,
            origin="first_party",
        )
        assert isinstance(report, ConformanceReport)
        assert report.passed

    def test_bad_estimator_report_fails(self):
        report = run_conformance(
            "bad_estimator",
            InferredNoErrorBound.ALEMS_PLUGIN_META,
            cls=InferredNoErrorBound,
            origin="first_party",
        )
        assert not report.passed

    def test_no_group_in_meta_returns_unknown(self):
        meta = {"plugin_id": "orphan", "family": "unknown", "version": "0.1.0"}
        report = run_conformance("orphan", meta, cls=None, origin="external")
        assert report.results[0].extension_point == "unknown"

    def test_no_cls_runs_manifest_only(self):
        # Without cls, only manifest check runs; should pass for good meta.
        report = run_conformance(
            "good_reader",
            GoodReader.ALEMS_PLUGIN_META,
            cls=None,
            origin="first_party",
        )
        assert report.passed

    def test_kit_exception_captured_as_failure(self):
        # Simulate a kit that raises internally.
        class ExplodingReader:
            ALEMS_PLUGIN_META = {
                "plugin_id": "bomb",
                "family": "measurement",
                "extension_point": "alems.readers.energy",
                "version": "0.1.0",
            }
            FIDELITY = "MEASURED"
            ERROR_BOUND = None
            @classmethod
            def is_available(cls):
                raise RuntimeError("hardware exploded")
            @classmethod
            def get_name(cls): return "bomb"
            @classmethod
            def get_config_schema(cls): return {}

        # Should not raise; exception captured as warning (is_available raises).
        report = run_conformance(
            "bomb",
            ExplodingReader.ALEMS_PLUGIN_META,
            cls=ExplodingReader,
            origin="first_party",
        )
        # is_available raising is a warning not a failure, so report passes.
        assert report.passed
        assert any("raised" in w for w in report.results[0].warnings)
