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
from .providers.gecko_v2 import GeckoV2Retriever
from .providers.pinnacle import PinnacleRetriever
from .providers.altenar import AltenarRetriever
from .providers.vbet import VbetRetriever
from .providers.interwetten import InterwettenRetriever
from .providers.coolbet import CoolbetRetriever
from .providers.tipwin import TipwinRetriever
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
        self._circuit_breaker = None  # Injected by orchestrator

    def set_circuit_breaker(self, circuit_breaker):
        """Inject circuit breaker for transport-level 429 detection."""
        self._circuit_breaker = circuit_breaker

    def clear_extractor_cache(self):
        """Clear all cached extractors.

        Must be called between extraction runs so that stale
        browser handles / closed connections are not reused.
        The orchestrator's finally block calls extractor.close(),
        which invalidates the instance — keeping it in the cache
        would hand the next run a dead reference.
        """
        count = len(self._extractor_cache)
        self._extractor_cache.clear()
        if count:
            logger.debug(f"Cleared extractor cache ({count} entries)")

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

    @property
    def config_loader(self):
        """Get config loader instance."""
        return self._config_loader

    def get_enabled_providers(self):
        """Get list of enabled provider IDs."""
        return self._config_loader.get_enabled_providers()

    def get_provider(self, provider_id: str):
        """Get provider configuration."""
        return self._config_loader.get_provider(provider_id)

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

        # Get rate limit config for transport
        rate_limit_config = self._config_loader.get_orchestrator_config().rate_limit

        if retriever_type == "kambi":
            retriever = KambiRetriever(
                config,
                circuit_breaker=self._circuit_breaker,
                rate_limit_config=rate_limit_config
            )
        elif retriever_type == "polymarket":
            retriever = PolymarketRetriever(config)
        elif retriever_type == "spectate":
            # Spectate providers (888sport, MrGreen) - headless mode works
            from .core import BrowserTransport
            transport = BrowserTransport(headless=True, circuit_breaker=self._circuit_breaker)
            retriever = SpectateRetriever(config, transport=transport)
        elif retriever_type == "gecko_v2":
            # Gecko V2 - API interception approach (faster than DOM parsing)
            # Using headless=True for better performance (2-3s faster per sport)
            from .core import BrowserTransport
            transport = BrowserTransport(headless=True, circuit_breaker=self._circuit_breaker)
            retriever = GeckoV2Retriever(config, transport=transport)
        elif retriever_type == "snabbare":
            # Snabbare - Sportradar MTS platform, WebSocket interception
            # Headed required: headless drops from ~900 to ~249 events (WS data not delivered)
            from .core import BrowserTransport
            from .providers.snabbare import SnabbareRetriever
            transport = BrowserTransport(headless=False, circuit_breaker=self._circuit_breaker)
            retriever = SnabbareRetriever(config, transport=transport)
        elif retriever_type == "pinnacle":
            retriever = PinnacleRetriever(config)
        elif retriever_type == "tenbet":
            # 10Bet - Playtech/Mojito SPA, DOM scraping with ta-* selectors
            from .core import BrowserTransport
            from .providers.tenbet import TenBetRetriever
            transport = BrowserTransport(headless=False, circuit_breaker=self._circuit_breaker)
            retriever = TenBetRetriever(config, transport=transport)
        elif retriever_type == "altenar":
            # Altenar platform - REST API extraction (no browser needed)
            retriever = AltenarRetriever(config)
        elif retriever_type == "betconstruct":
            # BetConstruct/Swarm WebSocket - direct API (no browser needed)
            retriever = VbetRetriever(config)
        elif retriever_type == "interwetten":
            # Interwetten SSR - browser-based DOM parsing (headed for Cloudflare)
            from .core import BrowserTransport
            transport = BrowserTransport(headless=False, circuit_breaker=self._circuit_breaker)
            retriever = InterwettenRetriever(config, transport=transport)
        elif retriever_type == "coolbet":
            # Coolbet - proprietary GAN Sports platform, Imperva-protected
            # Imperva blocks ALL Playwright-launched browsers (even real Chrome channel).
            # Must connect via CDP to an existing user Chrome instance.
            # Start Chrome with: chrome --remote-debugging-port=9222
            from .core import BrowserTransport
            transport = BrowserTransport(cdp_url='http://localhost:9222', circuit_breaker=self._circuit_breaker)
            retriever = CoolbetRetriever(config, transport=transport)
        elif retriever_type == "tipwin":
            # Tipwin - proprietary platform, browser-based API interception
            # Headless works fine (tested: 1,221 events vs 1,077 headed)
            from .core import BrowserTransport
            transport = BrowserTransport(headless=True, circuit_breaker=self._circuit_breaker)
            retriever = TipwinRetriever(config, transport=transport)
        elif retriever_type == "custom":
            # Custom provider implementations
            from .core import BrowserTransport
            transport = BrowserTransport(headless=True, circuit_breaker=self._circuit_breaker)

            if provider_id == "comeon":
                from .providers.comeon_multileague import ComeOnMultiLeagueRetriever
                retriever = ComeOnMultiLeagueRetriever(config, transport=transport)
            elif provider_id in ("hajper", "lyllo"):
                from .providers.hajper import HajperRetriever
                retriever = HajperRetriever(config, transport=transport)
            else:
                raise ValueError(f"Unknown custom provider '{provider_id}'")
        else:
            raise ValueError(f"Unknown retriever type '{retriever_type}' for {provider_id}")

        self._extractor_cache[provider_id] = retriever
        return retriever
