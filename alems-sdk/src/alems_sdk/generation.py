"""
alems_sdk.generation: model adapter contracts (D3.3 engines.text, engines.media).

Physical home since 39.5.1a. core.execution.adapters.base re-exports these
exact objects. The contracts are pure: the network and throughput helpers
(BaseAdapterMixin, psutil) are runtime behaviour and stay in core; core
adapters that use them inherit the mixin explicitly.

Adapters return dicts; the runtime persists them (adapters never write).
Zero imports from core or scripts (INV-14, D2.1).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict
from alems_sdk.config_schema import Configurable

class TextGenABC(Configurable, ABC):
    """
    Abstract base for all text generation adapters.

    Subclasses must implement call(), is_available(), get_name().
    The call() return dict shape is the single contract; keys never change.
    ENGINE_TYPE is the registry identity key: it names the adapter family,
    not an endpoint (endpoint, API key and model come from provider config).
    """

    ENGINE_TYPE: str = "unknown"

    def __init__(self, provider_config: Dict, model_config: Dict):
        """
        Initialise with split provider and model configuration.

        Args:
            provider_config: provider dict (type, base_url, api_key_env, ...).
            model_config: per model dict (model_id, max_tokens, temperature, ...).
        """
        self.provider_config = provider_config
        self.model_config = model_config
        # Flattened for convenience; defaults kept from the original contract.
        self.model_id = model_config.get("model_id", "unknown")
        self.max_tokens = model_config.get("max_tokens", 1024)
        self.temperature = model_config.get("temperature", 0.7)

    @abstractmethod
    def call(self, prompt: str, temperature: float) -> Dict[str, Any]:
        """
        Execute one non streaming inference call.

        Returns:
            dict with keys: content (str), tokens ({prompt, completion, total}),
            total_time_ms (float), phase_metrics (dict, standard phase metrics
            shape), bytes_sent, bytes_recv, tcp_retransmits (int, 0 for local).
        """

    @abstractmethod
    def is_available(self) -> bool:
        """True if usable right now. Returns False gracefully; never raises."""

    @abstractmethod
    def get_name(self) -> str:
        """Human readable adapter name for logging."""


class MediaABC(Configurable, ABC):
    """
    Abstract base for media adapters (TTS, STT, voice cloning).

    Subclasses must implement process(), is_available(), get_name().
    """

    ENGINE_TYPE: str = "unknown"

    def __init__(self, provider_config: Dict, model_config: Dict):
        """
        Args:
            provider_config: provider dict (env_path, type, ...).
            model_config: model dict (model_id, voice, sample_rate, ...).
        """
        self.provider_config = provider_config
        self.model_config = model_config
        self.model_id = model_config.get("model_id", "unknown")
        self.env_path = provider_config.get("env_path", "")

    @abstractmethod
    def process(self, input_data: Any, **kwargs) -> Dict[str, Any]:
        """
        Execute one media task.

        Returns:
            dict with at least content, duration_sec, total_time_ms;
            TTS and voice cloning also audio_bytes and sample_rate.
        """

    @abstractmethod
    def is_available(self) -> bool:
        """True if dependencies and env_path are usable. Never raises."""

    @abstractmethod
    def get_name(self) -> str:
        """Human readable adapter name."""


__all__ = ["TextGenABC", "MediaABC"]
