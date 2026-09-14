"""
================================================================================
EXTENSION REGISTRY — extension_registry Table Operations
================================================================================

PURPOSE:
    Manages the extension_registry table: records activation, deactivation,
    version history, and provides lookup methods for the ExtensionManager.

    This is the runtime component. The table itself is created by migration
    v089_extension_registry.sql — this module assumes the table exists.

WHY SEPARATE FROM MANAGER:
    ExtensionManager handles Python-level lifecycle (class loading, callback
    dispatch). ExtensionRegistry handles DB-level state (what is recorded
    in the database). Separating them keeps each class focused and testable
    in isolation.

AUTHOR: Deepak Panigrahy
================================================================================
"""

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class ExtensionRegistry:
    """
    Manages reads and writes to the extension_registry table.

    One instance per ExtensionManager, created during load_extensions().
    Requires the extension_registry table to already exist (created by
    migration v089_extension_registry.sql).
    """

    def __init__(self, db: object) -> None:
        """
        Initialize with a live database connection.

        Args:
            db: DatabaseInterface instance. Only db.conn (sqlite3
                connection) is used directly here, consistent with
                how other core modules access the DB.
        """
        # Use db.db.conn pattern matching how experiment_runner accesses
        # the underlying sqlite3 connection through the wrapper.
        self._conn = db.db.conn if hasattr(db, "db") else db.conn

    def is_registered(self, name: str) -> bool:
        """
        Check if an extension has been recorded in extension_registry.

        Returns True for any status (active, inactive, legacy) — the table
        row existing means the extension was previously activated on this
        machine and its tables may already exist.

        Args:
            name: Extension identity string.

        Returns:
            True if a row exists, False otherwise.
        """
        try:
            row = self._conn.execute(
                "SELECT 1 FROM extension_registry WHERE name = ? LIMIT 1",
                (name,),
            ).fetchone()
            return row is not None
        except Exception as exc:
            # extension_registry might not exist on pre-35D databases.
            # Return False so the caller attempts activation normally.
            logger.warning(
                "extension_registry lookup failed for '%s': %s "
                "(table may not exist yet — run alems_migrate.py)",
                name,
                exc,
            )
            return False

    def record_activation(
        self,
        name: str,
        version: str,
        migration_version: Optional[str] = None,
    ) -> None:
        """
        Insert or update an extension row as 'active'.

        Called after on_activate() succeeds. Uses INSERT OR REPLACE so
        reactivating a previously deactivated extension updates the row
        rather than failing on the PRIMARY KEY constraint.

        Args:
            name            : Extension identity string (PRIMARY KEY).
            version         : Extension version string.
            migration_version: Filename of the highest migration run, or None.
        """
        try:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO extension_registry
                    (name, version, activated_at, status, migration_version)
                VALUES (?, ?, datetime('now'), 'active', ?)
                """,
                (name, version, migration_version),
            )
            self._conn.commit()
            logger.debug("extension_registry: recorded activation of '%s' v%s", name, version)
        except Exception as exc:
            logger.error(
                "extension_registry: failed to record activation of '%s': %s",
                name,
                exc,
            )

    def record_deactivation(self, name: str) -> None:
        """
        Update an extension row to 'inactive' status.

        Called during deactivation flow (future — not yet wired to
        experiment_runner). Tables and data are never deleted; only
        the runtime status changes.

        Args:
            name: Extension identity string.
        """
        try:
            self._conn.execute(
                "UPDATE extension_registry SET status = 'inactive' WHERE name = ?",
                (name,),
            )
            self._conn.commit()
            logger.debug("extension_registry: recorded deactivation of '%s'", name)
        except Exception as exc:
            logger.error(
                "extension_registry: failed to record deactivation of '%s': %s",
                name,
                exc,
            )

    def get_all(self) -> List[Dict]:
        """
        Return all rows from extension_registry.

        Used by diagnostic tools and the future GUI to show registered
        extensions and their current status.

        Returns:
            List of dicts with keys: name, version, activated_at,
            status, migration_version.
        """
        try:
            rows = self._conn.execute(
                """
                SELECT name, version, activated_at, status, migration_version
                FROM extension_registry
                ORDER BY activated_at
                """
            ).fetchall()
            return [
                {
                    "name": r[0],
                    "version": r[1],
                    "activated_at": r[2],
                    "status": r[3],
                    "migration_version": r[4],
                }
                for r in rows
            ]
        except Exception as exc:
            logger.warning("extension_registry: get_all failed: %s", exc)
            return []

    def get_status(self, name: str) -> Optional[str]:
        """
        Return the status string for one extension.

        Args:
            name: Extension identity string.

        Returns:
            Status string ("active", "inactive", "legacy"), or None if
            the extension is not registered.
        """
        try:
            row = self._conn.execute(
                "SELECT status FROM extension_registry WHERE name = ? LIMIT 1",
                (name,),
            ).fetchone()
            return row[0] if row else None
        except Exception as exc:
            logger.warning(
                "extension_registry: get_status failed for '%s': %s", name, exc
            )
            return None
