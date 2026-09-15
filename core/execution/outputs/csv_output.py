#!/usr/bin/env python3
"""
================================================================================
CSV OUTPUT ADAPTER  —  core/execution/outputs/csv_output.py
================================================================================

PURPOSE:
    Reference OutputAdapterABC implementation. Exports run(s) to CSV
    using only DatabaseInterface's documented public methods
    (get_run, get_runs_by_experiment) — no direct SQL, no assumptions
    about adapter internals, works against SQLite or any future
    DatabaseInterface implementation identically.

    Read-only with respect to the DB (SPEC 35G Section 7) — never
    writes back to any table.

AUTHOR: Deepak Panigrahy
SPEC:   35G Section 7
================================================================================
"""

import csv
import json
import logging
from pathlib import Path
from typing import Any, Dict

from core.database.base import DatabaseInterface
from core.execution.outputs.abc import ExportResult, OutputAdapterABC

logger = logging.getLogger(__name__)

_EXPORT_DIR = Path("exports")  # matches app_settings.yaml paths.exports convention


def _flatten(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    JSON-serialize any nested dict/list value so csv.DictWriter can
    write it as a single cell. Scalar values pass through unchanged.
    """
    flat = {}
    for k, v in row.items():
        flat[k] = json.dumps(v) if isinstance(v, (dict, list)) else v
    return flat


class CSVOutputAdapter(OutputAdapterABC):
    """CSV export via csv.DictWriter. One row per run."""

    OUTPUT_FORMAT = "csv"

    def export_run(self, run_id: int, db: DatabaseInterface) -> ExportResult:
        row = db.get_run(run_id)
        if row is None:
            return ExportResult(
                format=self.OUTPUT_FORMAT, path=None, size_bytes=0,
                record_count=0, metadata={"error": f"run_id {run_id} not found"},
            )
        return self._write_csv(f"run_{run_id}.csv", [row])

    def export_experiment(self, exp_id: int, db: DatabaseInterface) -> ExportResult:
        rows = db.get_runs_by_experiment(exp_id)
        return self._write_csv(f"experiment_{exp_id}.csv", rows)

    def _write_csv(self, filename: str, rows: list) -> ExportResult:
        _EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        path = _EXPORT_DIR / filename

        if not rows:
            path.write_text("")
            return ExportResult(
                format=self.OUTPUT_FORMAT, path=str(path), size_bytes=0,
                record_count=0, metadata={"note": "no rows"},
            )

        flat_rows = [_flatten(r) for r in rows]
        # Union of all keys across rows — rows from different schema
        # versions may not share every column.
        fieldnames = sorted({k for r in flat_rows for k in r.keys()})

        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(flat_rows)

        size = path.stat().st_size
        return ExportResult(
            format=self.OUTPUT_FORMAT, path=str(path), size_bytes=size,
            record_count=len(rows), metadata={},
        )

    def get_name(self) -> str:
        return "csv"

    def get_format(self) -> str:
        return self.OUTPUT_FORMAT

    def is_available(self) -> bool:
        return True
