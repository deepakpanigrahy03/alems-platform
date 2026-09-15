#!/usr/bin/env python3
"""
================================================================================
JSON OUTPUT ADAPTER  —  core/execution/outputs/json_output.py
================================================================================

PURPOSE:
    Reference OutputAdapterABC implementation. Exports run(s) to JSON
    using only DatabaseInterface's documented public methods
    (get_run, get_runs_by_experiment). No flattening needed — JSON
    preserves nested structure natively, unlike CSV.

    Read-only with respect to the DB (SPEC 35G Section 7).

AUTHOR: Deepak Panigrahy
SPEC:   35G Section 7
================================================================================
"""

import json
import logging
from pathlib import Path

from core.database.base import DatabaseInterface
from core.execution.outputs.abc import ExportResult, OutputAdapterABC

logger = logging.getLogger(__name__)

_EXPORT_DIR = Path("exports")


class JSONOutputAdapter(OutputAdapterABC):
    """JSON export via json.dump. One file, list-of-dicts or single dict."""

    OUTPUT_FORMAT = "json"

    def export_run(self, run_id: int, db: DatabaseInterface) -> ExportResult:
        row = db.get_run(run_id)
        if row is None:
            return ExportResult(
                format=self.OUTPUT_FORMAT, path=None, size_bytes=0,
                record_count=0, metadata={"error": f"run_id {run_id} not found"},
            )
        return self._write_json(f"run_{run_id}.json", row, record_count=1)

    def export_experiment(self, exp_id: int, db: DatabaseInterface) -> ExportResult:
        rows = db.get_runs_by_experiment(exp_id)
        return self._write_json(f"experiment_{exp_id}.json", rows, record_count=len(rows))

    def _write_json(self, filename: str, data, record_count: int) -> ExportResult:
        _EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        path = _EXPORT_DIR / filename

        # default=str handles datetime and other non-JSON-native types
        # that may appear in run rows without raising.
        with path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)

        size = path.stat().st_size
        return ExportResult(
            format=self.OUTPUT_FORMAT, path=str(path), size_bytes=size,
            record_count=record_count, metadata={},
        )

    def get_name(self) -> str:
        return "json"

    def get_format(self) -> str:
        return self.OUTPUT_FORMAT

    def is_available(self) -> bool:
        return True
