"""
core/tests/test_quality_gate.py

Tests for WP 39.5a quality gate.

Verifies:
1. quality_enabled=False skips OutputQualityExtension.persist() and hallucination_detector.
2. quality_enabled=True calls persist() and hallucination_detector on failed attempts.
3. apply_config sets quality_enabled=False when quality section is absent.
4. apply_config sets quality_enabled=False when quality.enabled=false in YAML.
5. apply_config sets quality_enabled=True when quality.enabled=true in YAML.
6. _load_output_quality_extension returns an object with get_name() == output_quality.
7. Cheap scorers (normalized_score, pass_fail) are written regardless of quality_enabled.

Rule S: these tests use a fake DB and fake extension; no real runs, no golden impact.
"""

import argparse
import sqlite3
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_db(conn):
    # type: (sqlite3.Connection) -> object
    """Minimal DatabaseInterface stand-in backed by a real in-memory SQLite."""
    db = types.SimpleNamespace()
    db.db = types.SimpleNamespace(conn=conn)
    return db


def _seed_tables(conn):
    # type: (sqlite3.Connection) -> None
    """Create minimal tables that _run_quality_scoring reads and writes."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS goal_attempt (
            attempt_id INTEGER PRIMARY KEY,
            run_id INTEGER,
            goal_id INTEGER,
            outcome TEXT,
            normalized_score REAL,
            pass_fail INTEGER,
            span_id TEXT
        );
        CREATE TABLE IF NOT EXISTS runs (
            run_id INTEGER PRIMARY KEY,
            total_energy_uj INTEGER
        );
        CREATE TABLE IF NOT EXISTS goal_output (
            goal_id INTEGER,
            run_id INTEGER,
            attempt_id INTEGER,
            output_text TEXT,
            output_type TEXT,
            capture_method TEXT
        );
    """)
    conn.execute("INSERT INTO runs VALUES (1, 50000)")
    conn.execute(
        "INSERT INTO goal_attempt (attempt_id, run_id, goal_id, outcome) "
        "VALUES (10, 1, 42, 'failure')"
    )
    conn.commit()


def _make_computation(score=0.0, pass_fail=0, method="rubric"):
    # type: (float, int, str) -> object
    """Fake JudgmentComputation with the fields _run_quality_scoring reads."""
    result = types.SimpleNamespace(
        normalized_score=score,
        pass_fail=pass_fail,
        score_method=method,
        quality_id=None,
    )
    comp = types.SimpleNamespace(result=result)
    return comp


# ---------------------------------------------------------------------------
# Import under test (deferred so patching works)
# ---------------------------------------------------------------------------

def _import_runner():
    import core.execution.experiment_runner as runner
    return runner


# ---------------------------------------------------------------------------
# Tests: apply_config quality section
# ---------------------------------------------------------------------------

class TestApplyConfigQuality(unittest.TestCase):

    def _run(self, yaml_text):
        # type: (str) -> argparse.Namespace
        import tempfile, os, argparse
        from core.execution.experiment_config_loader import apply_config
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_text)
            path = f.name
        try:
            args = argparse.Namespace(config=path)
            apply_config(args)
            return args
        finally:
            os.unlink(path)

    def test_quality_absent_defaults_false(self):
        # type: () -> None
        """No quality section means quality_enabled=False."""
        args = self._run("study:\n  name: x\n  experiment_type: normal\n  experiment_goal: g\n")
        self.assertFalse(getattr(args, "quality_enabled", True))

    def test_quality_enabled_false(self):
        # type: () -> None
        """quality: enabled: false sets quality_enabled=False."""
        args = self._run(
            "study:\n  name: x\n  experiment_type: normal\n  experiment_goal: g\n"
            "quality:\n  enabled: false\n"
        )
        self.assertFalse(args.quality_enabled)

    def test_quality_enabled_true(self):
        # type: () -> None
        """quality: enabled: true sets quality_enabled=True."""
        args = self._run(
            "study:\n  name: x\n  experiment_type: normal\n  experiment_goal: g\n"
            "quality:\n  enabled: true\n"
        )
        self.assertTrue(args.quality_enabled)


# ---------------------------------------------------------------------------
# Tests: _load_output_quality_extension
# ---------------------------------------------------------------------------

class TestLoadOutputQualityExtension(unittest.TestCase):

    def test_loads_via_entry_point(self):
        # type: () -> None
        """Entry point output_quality resolves to an object with get_name()."""
        from core.execution.experiment_runner import _load_output_quality_extension
        ext = _load_output_quality_extension()
        if ext is None:
            self.skipTest("output_quality entry point not registered; run pip install -e . first")
        self.assertEqual(ext.get_name(), "output_quality")

    def test_returns_none_when_not_installed(self):
        # type: () -> None
        """Returns None gracefully when entry point group has no output_quality."""
        with patch("importlib.metadata.entry_points", return_value=[]):
            from core.execution import experiment_runner
            result = experiment_runner._load_output_quality_extension()
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Tests: _run_quality_scoring gate
# ---------------------------------------------------------------------------

class TestQualityScoringGate(unittest.TestCase):

    def setUp(self):
        # type: () -> None
        self.conn = sqlite3.connect(":memory:")
        _seed_tables(self.conn)
        self.db = _make_fake_db(self.conn)

    def tearDown(self):
        # type: () -> None
        self.conn.close()

    def _result(self):
        # type: () -> dict
        return {
            "task_meta": {
                "id": "gsm8k_basic",
                "category": "math",
                "expected_answer": "42",
            },
            "execution": {"response": "wrong answer"},
        }

    def test_quality_disabled_skips_persist_and_hallucination(self):
        # type: () -> None
        """quality_enabled=False: persist() and hallucination_detector never called."""
        fake_ext = MagicMock()
        fake_detector = MagicMock()
        fake_computation = _make_computation(score=0.0, pass_fail=0)
        fake_engine = MagicMock()
        fake_engine.judge.return_value = fake_computation

        import core.execution.experiment_runner as runner
        original_ext = runner._output_quality_extension
        original_det = runner._hallucination_detector

        try:
            runner._output_quality_extension = fake_ext
            runner._hallucination_detector = fake_detector

            with patch.object(runner, "judgment_engine", fake_engine):
                runner._run_quality_scoring(
                    db=self.db,
                    goal_id=42,
                    result=self._result(),
                    workflow_type="agentic",
                    conn=self.conn,
                    quality_enabled=False,
                )

            fake_ext.persist.assert_not_called()
            fake_detector.detect.assert_not_called()
        finally:
            runner._output_quality_extension = original_ext
            runner._hallucination_detector = original_det

    def test_quality_enabled_calls_persist_and_hallucination(self):
        # type: () -> None
        """quality_enabled=True: persist() called; hallucination_detector called on fail."""
        fake_ext = MagicMock()
        fake_ext.persist.return_value = 99
        fake_detector = MagicMock()
        fake_computation = _make_computation(score=0.0, pass_fail=0)
        fake_engine = MagicMock()
        fake_engine.judge.return_value = fake_computation

        import core.execution.experiment_runner as runner
        original_ext = runner._output_quality_extension
        original_det = runner._hallucination_detector

        try:
            runner._output_quality_extension = fake_ext
            runner._hallucination_detector = fake_detector

            with patch.object(runner, "judgment_engine", fake_engine):
                runner._run_quality_scoring(
                    db=self.db,
                    goal_id=42,
                    result=self._result(),
                    workflow_type="agentic",
                    conn=self.conn,
                    quality_enabled=True,
                )

            fake_ext.persist.assert_called_once()
            fake_detector.detect.assert_called_once()
        finally:
            runner._output_quality_extension = original_ext
            runner._hallucination_detector = original_det

    def test_cheap_scorer_writes_regardless_of_quality_enabled(self):
        # type: () -> None
        """normalized_score and pass_fail written to goal_attempt even when quality_enabled=False."""
        fake_ext = MagicMock()
        fake_computation = _make_computation(score=0.85, pass_fail=1)
        fake_engine = MagicMock()
        fake_engine.judge.return_value = fake_computation

        import core.execution.experiment_runner as runner
        original_ext = runner._output_quality_extension

        try:
            runner._output_quality_extension = fake_ext

            with patch.object(runner, "judgment_engine", fake_engine):
                runner._run_quality_scoring(
                    db=self.db,
                    goal_id=42,
                    result=self._result(),
                    workflow_type="agentic",
                    conn=self.conn,
                    quality_enabled=False,
                )

            row = self.conn.execute(
                "SELECT normalized_score, pass_fail FROM goal_attempt WHERE attempt_id=10"
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertAlmostEqual(row[0], 0.85, places=2)
            self.assertEqual(row[1], 1)
            fake_ext.persist.assert_not_called()
        finally:
            runner._output_quality_extension = original_ext


# ---------------------------------------------------------------------------
# Tests: entry point population for harness groups
# ---------------------------------------------------------------------------

class TestHarnessEntryPoints(unittest.TestCase):

    def test_harness_retry_entries_exist(self):
        # type: () -> None
        """alems.harness.retry has flat and ear after pip install -e ."""
        from importlib.metadata import entry_points
        eps = {ep.name for ep in entry_points(group="alems.harness.retry")}
        if not eps:
            self.skipTest("Entry points not registered; run pip install -e . first")
        self.assertIn("flat", eps)
        self.assertIn("ear", eps)

    def test_harness_recovery_entries_exist(self):
        # type: () -> None
        """alems.harness.recovery has full_restart and localized."""
        from importlib.metadata import entry_points
        eps = {ep.name for ep in entry_points(group="alems.harness.recovery")}
        if not eps:
            self.skipTest("Entry points not registered; run pip install -e . first")
        self.assertIn("full_restart", eps)
        self.assertIn("localized", eps)

    def test_harness_collectors_entries_exist(self):
        # type: () -> None
        """alems.harness.collectors has noop and engine_backed."""
        from importlib.metadata import entry_points
        eps = {ep.name for ep in entry_points(group="alems.harness.collectors")}
        if not eps:
            self.skipTest("Entry points not registered; run pip install -e . first")
        self.assertIn("noop", eps)
        self.assertIn("engine_backed", eps)


if __name__ == "__main__":
    unittest.main()
