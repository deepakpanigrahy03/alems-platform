"""
================================================================================
TEST SUITE — 35D Extension System
================================================================================

Tests for ExtensionABC, PostRunPayload, ExtensionManager, and
OutputQualityExtension.

Run with:
    python3 -m pytest tests/test_extension_system.py -v

Requires: Python 3.9+, pytest, PyYAML.
No hardware required — all tests use in-memory SQLite.

AUTHOR: Deepak Panigrahy
================================================================================
"""

import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from core.extensions.abc import ExtensionABC, PostRunPayload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(extra_sql: str = "") -> sqlite3.Connection:
    """Create an in-memory SQLite DB with the minimal tables tests need."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE runs (
            run_id    INTEGER PRIMARY KEY,
            exp_id    INTEGER,
            hw_id     INTEGER,
            energy_uj INTEGER,
            status    TEXT
        );
        CREATE TABLE schema_version (
            version    INTEGER PRIMARY KEY,
            applied_at TEXT,
            description TEXT
        );
        CREATE TABLE extension_registry (
            name              TEXT PRIMARY KEY,
            version           TEXT NOT NULL,
            activated_at      TEXT NOT NULL,
            status            TEXT NOT NULL DEFAULT 'active',
            migration_version TEXT
        );
        CREATE TABLE migration_history (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            version          INTEGER,
            type             TEXT,
            filename         TEXT,
            checksum_sha256  TEXT,
            tool_version     TEXT,
            duration_ms      INTEGER,
            status           TEXT,
            hostname         TEXT,
            machine_id       TEXT,
            repo_commit      TEXT,
            source           TEXT DEFAULT 'core'
        );
        """
        + extra_sql
    )
    return conn


def _make_fake_db(conn: sqlite3.Connection) -> Any:
    """Build a minimal db wrapper matching the db.db.conn access pattern."""
    inner = MagicMock()
    inner.conn = conn
    outer = MagicMock()
    outer.db = inner
    outer.conn = conn  # also expose top-level conn for compatibility
    return outer


def _make_payload(conn: sqlite3.Connection, **overrides) -> PostRunPayload:
    """Build a PostRunPayload with sensible defaults."""
    fake_db = _make_fake_db(conn)
    defaults = dict(
        run_id=1,
        exp_id=1,
        hw_id=1,
        workflow_type="agentic",
        model_name="test-model",
        energy_uj=54_480_000,
        duration_ns=18_500_000_000,
        status="completed",
        baseline_id="test_baseline",
        db=fake_db,
    )
    defaults.update(overrides)
    return PostRunPayload(**defaults)


# ---------------------------------------------------------------------------
# PostRunPayload tests
# ---------------------------------------------------------------------------

class TestPostRunPayload:

    def test_payload_is_frozen(self):
        """PostRunPayload must raise AttributeError on any field assignment."""
        conn = _make_db()
        payload = _make_payload(conn)
        with pytest.raises(AttributeError):
            payload.energy_uj = 999_999

    def test_payload_fields_readable(self):
        """All payload fields must be readable after construction."""
        conn = _make_db()
        payload = _make_payload(conn, run_id=42, energy_uj=12_000_000)
        assert payload.run_id == 42
        assert payload.energy_uj == 12_000_000
        assert payload.workflow_type == "agentic"
        assert payload.status == "completed"

    def test_payload_duration_ns_frozen(self):
        """duration_ns field must also be immutable."""
        conn = _make_db()
        payload = _make_payload(conn)
        with pytest.raises(AttributeError):
            payload.duration_ns = 0

    def test_payload_status_frozen(self):
        """status field must be immutable."""
        conn = _make_db()
        payload = _make_payload(conn)
        with pytest.raises(AttributeError):
            payload.status = "failed"


# ---------------------------------------------------------------------------
# ExtensionABC contract tests
# ---------------------------------------------------------------------------

class _ConcreteExtension(ExtensionABC):
    """Minimal concrete implementation for contract testing."""

    EXTENSION_VERSION = "0.1.0"

    def get_name(self) -> str:
        return "test_ext"

    def get_version(self) -> str:
        return self.EXTENSION_VERSION

    def get_migrations_dir(self) -> Optional[Path]:
        return None

    def get_tables(self) -> List[str]:
        return ["test_ext_table"]

    def get_config_schema(self) -> Dict:
        return {}

    def on_activate(self, db: Any) -> None:
        pass

    def on_deactivate(self, db: Any) -> None:
        pass

    def on_post_run(self, payload: PostRunPayload) -> None:
        # Write a marker to verify on_post_run was called.
        conn = payload.db.db.conn
        conn.execute(
            "INSERT INTO test_ext_table (run_id) VALUES (?)",
            (payload.run_id,),
        )
        conn.commit()


class TestExtensionABC:

    def test_concrete_extension_instantiates(self):
        """A complete ExtensionABC implementation must instantiate without error."""
        ext = _ConcreteExtension()
        assert ext.get_name() == "test_ext"
        assert ext.get_version() == "0.1.0"

    def test_on_post_run_called(self):
        """on_post_run must write to the extension's own table."""
        conn = _make_db(
            "CREATE TABLE test_ext_table (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER);"
        )
        conn.execute("INSERT INTO runs VALUES (1, 1, 1, 54000000, 'completed')")
        conn.commit()

        payload = _make_payload(conn, run_id=1)
        ext = _ConcreteExtension()
        ext.on_post_run(payload)

        row = conn.execute(
            "SELECT run_id FROM test_ext_table WHERE run_id = 1"
        ).fetchone()
        assert row is not None, "on_post_run did not write to test_ext_table"
        assert row[0] == 1

    def test_incomplete_abc_raises(self):
        """Attempting to instantiate an incomplete ABC subclass must raise TypeError."""
        class IncompleteExtension(ExtensionABC):
            EXTENSION_VERSION = "1.0.0"
            def get_name(self) -> str:
                return "incomplete"
            # Missing all other abstract methods

        with pytest.raises(TypeError):
            IncompleteExtension()


# ---------------------------------------------------------------------------
# ExtensionManager tests
# ---------------------------------------------------------------------------

class TestExtensionManager:

    def test_legacy_mode_when_no_extensions_key(self, tmp_path):
        """Legacy mode when [extensions] key is absent from app_settings.yaml."""
        settings = tmp_path / "app_settings.yaml"
        settings.write_text("database:\n  engine: sqlite\n")

        from core.extensions.manager import ExtensionManager
        manager = ExtensionManager(config_path=str(settings))
        assert manager.is_legacy_mode() is True

    def test_selective_mode_when_extensions_key_present(self, tmp_path):
        """Selective mode when [extensions] active is present (even if empty)."""
        settings = tmp_path / "app_settings.yaml"
        settings.write_text("extensions:\n  active: []\n")

        from core.extensions.manager import ExtensionManager
        manager = ExtensionManager(config_path=str(settings))
        assert manager.is_legacy_mode() is False

    def test_active_names_parsed_correctly(self, tmp_path):
        """Active extension names are parsed from [extensions] active list."""
        settings = tmp_path / "app_settings.yaml"
        settings.write_text(
            "extensions:\n  active:\n    - output_quality\n    - orchestration\n"
        )

        from core.extensions.manager import ExtensionManager
        manager = ExtensionManager(config_path=str(settings))
        assert manager.is_legacy_mode() is False
        # Names are read; extensions are not loaded until load_extensions() is called.
        assert manager._active_names == ["output_quality", "orchestration"]

    def test_run_post_run_no_op_in_legacy_mode(self, tmp_path):
        """run_post_run must be a complete no-op in legacy mode."""
        settings = tmp_path / "app_settings.yaml"
        settings.write_text("database:\n  engine: sqlite\n")

        from core.extensions.manager import ExtensionManager
        manager = ExtensionManager(config_path=str(settings))

        conn = _make_db()
        payload = _make_payload(conn)
        # Must not raise, must not call any extension.
        manager.run_post_run(payload)

    def test_missing_config_file_gives_legacy_mode(self, tmp_path):
        """Missing app_settings.yaml defaults to legacy mode (safe)."""
        from core.extensions.manager import ExtensionManager
        manager = ExtensionManager(config_path=str(tmp_path / "nonexistent.yaml"))
        assert manager.is_legacy_mode() is True


# ---------------------------------------------------------------------------
# OutputQualityExtension tests
# ---------------------------------------------------------------------------

class TestOutputQualityExtension:

    def _make_db_with_quality_tables(self) -> sqlite3.Connection:
        """Create in-memory DB with output_quality and run_quality tables."""
        return _make_db(
            """
            INSERT INTO runs VALUES (1, 1, 1, 54480000, 'completed');
            CREATE TABLE output_quality (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id           INTEGER NOT NULL,
                task_id          TEXT,
                scorer_name      TEXT NOT NULL,
                score            REAL,
                scorer_energy_uj INTEGER,
                evaluated_at     TEXT NOT NULL,
                notes            TEXT
            );
            CREATE TABLE run_quality (
                run_id                 INTEGER PRIMARY KEY,
                avg_score              REAL,
                scorer_count           INTEGER,
                total_scorer_energy_uj INTEGER,
                computed_at            TEXT NOT NULL
            );
            """
        )

    def test_stub_writes_output_quality_row(self):
        """on_post_run must write a stub row to output_quality."""
        conn = self._make_db_with_quality_tables()
        payload = _make_payload(conn, run_id=1)

        from extensions.output_quality.extension import OutputQualityExtension
        ext = OutputQualityExtension()
        ext.on_post_run(payload)

        row = conn.execute(
            "SELECT scorer_name, score FROM output_quality WHERE run_id = 1"
        ).fetchone()
        assert row is not None, "output_quality row not written"
        assert row[0] == "stub_phase1"
        assert row[1] is None  # score is NULL in phase 1

    def test_stub_writes_run_quality_row(self):
        """on_post_run must write a summary row to run_quality."""
        conn = self._make_db_with_quality_tables()
        payload = _make_payload(conn, run_id=1)

        from extensions.output_quality.extension import OutputQualityExtension
        ext = OutputQualityExtension()
        ext.on_post_run(payload)

        row = conn.execute(
            "SELECT scorer_count FROM run_quality WHERE run_id = 1"
        ).fetchone()
        assert row is not None, "run_quality row not written"
        assert row[0] == 0  # no actual scorer ran in phase 1

    def test_skips_failed_runs(self):
        """on_post_run must skip runs with status != 'completed'."""
        conn = self._make_db_with_quality_tables()
        payload = _make_payload(conn, run_id=1, status="failed")

        from extensions.output_quality.extension import OutputQualityExtension
        ext = OutputQualityExtension()
        ext.on_post_run(payload)

        row = conn.execute(
            "SELECT id FROM output_quality WHERE run_id = 1"
        ).fetchone()
        assert row is None, "output_quality row should not be written for failed runs"

    def test_get_name_returns_correct_identity(self):
        """get_name() must return the stable registry identity string."""
        from extensions.output_quality.extension import OutputQualityExtension
        ext = OutputQualityExtension()
        assert ext.get_name() == "output_quality"

    def test_get_tables_returns_owned_tables(self):
        """get_tables() must list the tables created by this extension's migrations."""
        from extensions.output_quality.extension import OutputQualityExtension
        ext = OutputQualityExtension()
        tables = ext.get_tables()
        assert "output_quality" in tables
        assert "run_quality" in tables

    def test_on_post_run_does_not_raise_on_db_error(self):
        """on_post_run must catch exceptions and not propagate to experiment_runner."""
        conn = _make_db()  # No output_quality table — will cause INSERT error
        payload = _make_payload(conn, run_id=1)

        from extensions.output_quality.extension import OutputQualityExtension
        ext = OutputQualityExtension()
        # Must not raise even though the table does not exist.
        ext.on_post_run(payload)


# ---------------------------------------------------------------------------
# ExtensionRegistry tests
# ---------------------------------------------------------------------------

class TestExtensionRegistry:

    def test_is_registered_returns_false_for_unknown(self):
        """is_registered must return False for names not in extension_registry."""
        conn = _make_db()
        fake_db = _make_fake_db(conn)

        from core.extensions.registry import ExtensionRegistry
        reg = ExtensionRegistry(fake_db)
        assert reg.is_registered("nonexistent_ext") is False

    def test_record_and_check_activation(self):
        """record_activation must insert a row that is_registered then finds."""
        conn = _make_db()
        fake_db = _make_fake_db(conn)

        from core.extensions.registry import ExtensionRegistry
        reg = ExtensionRegistry(fake_db)
        reg.record_activation("output_quality", "1.0.0", "e001_create.sql")

        assert reg.is_registered("output_quality") is True
        assert reg.get_status("output_quality") == "active"

    def test_record_deactivation_changes_status(self):
        """record_deactivation must set status to 'inactive'."""
        conn = _make_db()
        fake_db = _make_fake_db(conn)

        from core.extensions.registry import ExtensionRegistry
        reg = ExtensionRegistry(fake_db)
        reg.record_activation("output_quality", "1.0.0", None)
        reg.record_deactivation("output_quality")

        assert reg.get_status("output_quality") == "inactive"

    def test_get_all_returns_registered_entries(self):
        """get_all must return all rows inserted into extension_registry."""
        conn = _make_db()
        fake_db = _make_fake_db(conn)

        from core.extensions.registry import ExtensionRegistry
        reg = ExtensionRegistry(fake_db)
        reg.record_activation("output_quality", "1.0.0", "e001.sql")
        reg.record_activation("orchestration", "1.0.0", None)

        all_ext = reg.get_all()
        names = [e["name"] for e in all_ext]
        assert "output_quality" in names
        assert "orchestration" in names
