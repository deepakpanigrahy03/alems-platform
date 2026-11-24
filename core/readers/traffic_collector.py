"""
traffic_collector.py: start and stop every available traffic reader plugin
with the energy window.

Why this exists:
    Memory traffic (bytes moved by the GPU, over links, by copy engines) is a
    measurement family of its own: a run can have none, one or several traffic
    readers. Core knows no traffic tool by name: readers register under the
    entry point group alems.readers.traffic (core/readers/bootstrap.py) and
    implement alems_sdk.readers.TrafficReaderABC. This collector runs all of
    them that report available, buffers what they return, and never touches
    the database (EEI-1, EEI-3): the harness passes the results to persistence
    in the run result.

Never raises: a reader that fails to start or stop is reported with status
'failed' and its reason, and the run stays valid.
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class TrafficCollector:
    """Runs all available traffic readers for one measurement window."""

    def __init__(self):
        # type: () -> None
        self._active = []  # type: List[Any]
        self._results = []  # type: List[Dict[str, Any]]

    def start(self):
        # type: () -> None
        """Instantiate registered traffic readers and start the available ones."""
        self._active, self._results = [], []
        for name, cls in self._registered().items():
            reader = self._instantiate(name, cls)
            if reader is None:
                continue
            if not reader.is_available():
                self._results.append(reader.unavailable_result())
                continue
            try:
                reader.start()
                self._active.append(reader)
            except Exception as exc:  # reader refused: recorded, run continues
                logger.warning("traffic reader %s failed to start: %s", name, exc)
                self._results.append(reader.failed_result(str(exc)))

    def stop(self):
        # type: () -> List[Dict[str, Any]]
        """Stop started readers; return one result dict per reader."""
        for reader in self._active:
            try:
                self._results.append(reader.stop())
            except Exception as exc:
                logger.warning("traffic reader %s failed to stop: %s", reader.get_name(), exc)
                self._results.append(reader.failed_result(str(exc)))
        results, self._active, self._results = self._results, [], []
        return results

    @staticmethod
    def _registered():
        # type: () -> Dict[str, Any]
        """Traffic reader classes from the registry; empty when none."""
        try:
            from core.readers.bootstrap import traffic_registry
            return dict(traffic_registry.get_all())
        except Exception as exc:
            logger.warning("traffic readers: registry unavailable: %s", exc)
            return {}

    @staticmethod
    def _instantiate(name, cls):
        # type: (str, Any) -> Any
        """Reader instance; None (logged) when the plugin cannot be built."""
        try:
            return cls()
        except Exception as exc:
            logger.warning("traffic reader %s not constructible: %s", name, exc)
            return None
