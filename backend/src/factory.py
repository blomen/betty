"""
Extractor Factory

Singleton factory for creating extractors based on provider configuration.
Uses centralized ConfigLoader for configuration management.
"""

import logging
from typing import Dict

from .core import Retriever
from .providers.kambi import KambiRetriever
from .providers.polymarket import PolymarketRetriever
from .providers.spectate import SpectateRetriever
from .config import ConfigLoader, SportConfig, ProviderConfig

logger = logging.getLogger(__name__)


class ExtractorFactory:
    """
    Factory for creating extractors based on provider configuration.

    Uses singleton pattern to ensure consistent config across the app.
    Delegates configuration loading to ConfigLoader.
    """
    _instance = None

    def __init__(self):
        self._config_loader = ConfigLoader.get_instance()
        self._extractor_cache: Dict[str, Retriever] = {}

    @classmethod
    def get_instance(cls) -> "ExtractorFactory":
        """Get or create the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def sports(self):
        """Get sports configuration."""
        return self._config_loader.sports

    @property
    def providers(self):
        """Get providers configuration (as dict for backward compatibility)."""
        # Return as dict for backward compatibility
        return {pid: config.model_dump() for pid, config in self._config_loader.providers.items()}

    def get_enabled_providers(self):
        """Get list of enabled provider IDs."""
        return self._config_loader.get_enabled_providers()

    def get_extractor(self, provider_id: str) -> Retriever:
        """
        Get or create an extractor for the given provider.

        Args:
            provider_id: Provider identifier (e.g., "unibet", "polymarket")

        Returns:
            Configured Retriever instance

        Raises:
            ValueError: If provider not found or not active
        """
        # Return cached instance if available
        if provider_id in self._extractor_cache:
            return self._extractor_cache[provider_id]

        provider_config = self._config_loader.get_provider(provider_id)
        if not provider_config:
            raise ValueError(f"Provider '{provider_id}' not found or not active")

        # Convert ProviderConfig to dict for backward compatibility
        config = provider_config.model_dump()

        retriever_type = provider_config.retriever_type

        # Create appropriate retriever based on type
        retriever: Retriever = None

        if retriever_type == "kambi":
            retriever = KambiRetriever(config)
        elif retriever_type == "polymarket":
            # Inject sports map from config loader
            sports_map = self._config_loader.get_sports_map_for_polymarket()
            retriever = PolymarketRetriever(config, sports_map=sports_map)
        elif retriever_type == "spectate":
            retriever = SpectateRetriever(config)
        elif retriever_type == "snabbare":
            from .providers.snabbare import SnabbareRetriever
            retriever = SnabbareRetriever(config)
        else:
            raise ValueError(f"Unknown retriever type '{retriever_type}' for {provider_id}")

        self._extractor_cache[provider_id] = retriever
        return retriever
