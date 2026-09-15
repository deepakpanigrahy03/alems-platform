#!/usr/bin/env python3
"""
================================================================================
OUTPUT ADAPTER ABC  —  core/execution/outputs/abc.py
================================================================================

PURPOSE:
    Interface for pluggable export formats (CSV, JSON, Parquet, OTel,
    W3C PROV, Prometheus, ...). Exact OUTPUT_FORMAT string match,
    same pattern as scorers/tools/frameworks.

    NOT INCLUDED HERE: concrete CSVOutputAdapter / JSONOutputAdapter
    implementations, and bootstrap.py. Writing real export code
    against DatabaseInterface requires reviewing its actual query
    methods first (core/database/base.py, core/database/sqlite_adapter.py)
    rather than guessing method names — deferred pending that review.

AUTHOR: Deepak Panigrahy
SPEC:   35G Section 7
================================================================================
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from core.database.base import DatabaseInterface


@dataclass
class ExportResult:
    format: str
    path: Optional[str]
    size_bytes: int
    record_count: int
    metadata: Dict[str, Any] = field(default_factory=dict)


class OutputAdapterABC(ABC):
    """
    Base class for all output/export adapters.

    Subclasses declare OUTPUT_FORMAT as a class attribute
    (e.g. "csv", "json", "parquet", "otel") and are registered
    into OutputRegistry keyed by that string.

    Output adapters are read-only with respect to core: they read
    from DatabaseInterface and produce output in their format. They
    never write back to the DB (SPEC 35G Section 7).
    """

    OUTPUT_FORMAT: str = ""

    @abstractmethod
    def export_run(self, run_id: int, db: DatabaseInterface) -> ExportResult:
        raise NotImplementedError

    @abstractmethod
    def export_experiment(self, exp_id: int, db: DatabaseInterface) -> ExportResult:
        raise NotImplementedError

    @abstractmethod
    def get_name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def get_format(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def is_available(self) -> bool:
        raise NotImplementedError

    def get_config_schema(self) -> Dict[str, Any]:
        return {}
