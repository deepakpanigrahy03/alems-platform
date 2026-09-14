"""
================================================================================
OLLAMA ADAPTER — Proof of Concept External Plugin (SPEC 35E)
================================================================================

PURPOSE:
    First external plugin package. Proves entry_point discovery works
    end to end: pip install alems-plugin-ollama makes this class appear
    in text_registry without any core code change.

AUTHOR: Deepak Panigrahy
================================================================================
"""

import logging
from typing import Any, Dict, Optional

import requests

from core.execution.adapters.base import TextGenABC

logger = logging.getLogger(__name__)


class OllamaAdapter(TextGenABC):
    """
    Adapter for a local Ollama server.

    ENGINE_TYPE matches the entry point name declared in this package's
    pyproject.toml — the two must agree or the plugin registers under
    one name but is selected by another.
    """

    ENGINE_TYPE = "ollama"
    METHOD_ID = "ollama"  # AdapterRegistry.register() keys on METHOD_ID
                          # for every family, engines included (see 35B).

    def __init__(self, config: Dict[str, Any]):
        # host/model come from provider config, not hardcoded, per
        # COMPLIANCE.md CQC-6 (no hardcoded paths/hosts).
        self._host = config.get("host", "http://localhost:11434")
        self._model = config.get("model", "llama3")

    def call(self, prompt: str, **kwargs) -> Dict[str, Any]:
        """
        Send prompt to the Ollama REST API and return the result.

        Never raises on a network failure — returns an error dict so
        the measurement harness can record a failed call rather than
        crashing the run (DC-3: no silent failures, but also no
        uncaught exceptions from an external adapter).
        """
        try:
            resp = requests.post(
                f"{self._host}/api/generate",
                json={"model": self._model, "prompt": prompt, "stream": False},
                timeout=kwargs.get("timeout", 60),
            )
            resp.raise_for_status()
            data = resp.json()
            return {"text": data.get("response", ""), "raw": data}
        except Exception as exc:
            logger.warning("OllamaAdapter: call failed: %s", exc)
            return {"text": "", "error": str(exc)}

    def is_available(self) -> bool:
        """Return True if the Ollama server responds to a health check."""
        try:
            resp = requests.get(f"{self._host}/api/tags", timeout=3)
            return resp.status_code == 200
        except Exception:
            return False

    def get_name(self) -> str:
        return f"OllamaAdapter({self._model} @ {self._host})"
