"""
Configuration Loader

Centralized configuration loading with validation.
Loads providers.yaml and sports.json with schema validation.
"""

import logging
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


# ============ Pydantic Models for Validation ============


class SportConfig(BaseModel):
    """Configuration for a sport."""

    key: str  # Canonical sport identifier (e.g., 'football', 'basketball')
    name: str  # Display name
    kambi_sport: str  # Kambi API sport identifier
    pinnacle_sport_id: int | None = None  # Pinnacle API sport ID


class ProviderConfig(BaseModel):
    """Configuration for a betting provider."""

    id: str
    name: str | None = None
    retriever_type: str
    domain: str | None = None
    api_base: str | None = None
    base_url: str | None = None
    brand: str | None = None
    params: dict = Field(default_factory=dict)
    max_leagues: int | None = None  # For multi-league providers like ComeOn
    concurrent_leagues: int | None = 3  # Number of parallel league extractions (default 3)

    # Fields used by various providers (must be declared to avoid Pydantic dropping them)
    integration: str | None = None  # Altenar skin ID
    supported_sports: list[str] | None = None
    bonus: dict | None = None
    sharp: bool | None = False
    site_url: str | None = None

    # Currency (default SEK for Swedish sportsbooks, USDC for Polymarket)
    currency: str | None = "SEK"
    exchange_rate_sek: float | None = 1.0  # 1 unit of currency = X SEK

    # Secondary URL (e.g., Polymarket CLOB API)
    clob_url: str | None = None

    # Gecko V2 session init path (default /sv/odds, bethard needs /sv/sports)
    init_path: str | None = None

    # Per-provider timeout override (seconds). None = use global provider_timeout.
    provider_timeout: int | None = None

    # Per-provider sport timeout override (seconds). None = use global sport_timeout.
    sport_timeout: int | None = None

    # API key for providers that need auth (e.g., Cloudbet affiliate key)
    api_key: str | None = None

    # ComeOn-specific depth extraction configuration
    extract_full_markets: bool | None = False  # Enable event detail page extraction
    concurrent_event_details: int | None = 10  # Parallel event detail page loads
    detail_extraction_filter: str | None = "all"  # "all", "popular", or "none"
    sports_to_extract: str | list[str] | None = None  # Sports to extract ("all" or list)


class AppConfig(BaseModel):
    """Root application configuration."""

    sports: list[SportConfig]
    providers: dict[str, ProviderConfig]
    active_providers: list[str] = Field(default_factory=list)

    @field_validator("providers", mode="before")
    def add_provider_ids(cls, v):
        """Ensure provider IDs are set."""
        if isinstance(v, dict):
            for pid, config in v.items():
                if isinstance(config, dict):
                    config["id"] = pid
        return v


class RetryConfig(BaseModel):
    """Retry logic configuration."""

    enabled: bool = True
    max_retries: int = 3
    initial_backoff_seconds: float = 2.0
    max_backoff_seconds: float = 60.0
    exponential_base: float = 2.0
    retry_on_timeout: bool = True


class CircuitBreakerConfig(BaseModel):
    """Circuit breaker configuration."""

    enabled: bool = True
    failure_threshold: int = 5
    recovery_timeout_seconds: int = 300
    half_open_max_attempts: int = 3


class CacheConfig(BaseModel):
    """Response caching configuration."""

    enabled: bool = True
    ttl_seconds: int = 300
    max_entries: int = 1000
    cache_layer: str = "transport"  # "transport" or "orchestrator"
    cache_per_provider: bool = True


class HealthCheckConfig(BaseModel):
    """Provider health check configuration."""

    enabled: bool = True
    strategy: str = "on_demand"
    timeout_seconds: float = 10.0
    check_before_extraction: bool = True


class MetricsConfig(BaseModel):
    """Performance metrics configuration."""

    enabled: bool = True
    track_timing: bool = True
    track_success_rate: bool = True
    track_cache_hit_rate: bool = True
    persist_to_db: bool = True
    retention_count: int = 100


class ProgressConfig(BaseModel):
    """Progress reporting configuration."""

    enabled: bool = True
    transport: str = "callback"  # "callback" or "websocket"
    websocket_path: str = "/ws/extraction"


class GracefulShutdownConfig(BaseModel):
    """Graceful shutdown configuration."""

    enabled: bool = True
    shutdown_timeout_seconds: int = 30
    cancel_pending_tasks: bool = True


class FuzzyMatchConfig(BaseModel):
    """Fuzzy matching configuration."""

    threshold: int = 85  # Minimum average match score (0-100)
    min_individual_score: int = 75  # Minimum score for EACH team (0-100)
    max_asymmetry_diff: int = 25  # Max allowed difference between team scores
    min_for_asymmetry_check: int = 80  # Only reject asymmetry if min score below this
    prefix_filter_length: int = 3  # Chars for prefix pre-filtering (0 to disable)


class RateLimitConfig(BaseModel):
    """Transport-level rate limit handling."""

    max_retries: int = 2  # Max retries on 429
    default_wait_seconds: int = 5  # Default wait if no Retry-After header
    max_wait_seconds: int = 60  # Cap on wait time
    notify_circuit_breaker_after: int = 2  # Notify circuit breaker after N consecutive 429s


class ProviderGroupConfig(BaseModel):
    """Provider group with shared resource constraints."""

    name: str
    retriever_types: list[str]  # ["kambi"] or ["gecko_v2", "spectate"]
    max_concurrent: int = 3
    shared_resource: str = "none"  # "api", "browser", "none"
    health_check_delay_ms: int = 0  # Delay between health checks in group
    post_extraction_delay_ms: int = 0  # Delay after each provider extraction (rate limit recovery)


class OrchestratorConfig(BaseModel):
    """Global orchestrator configuration."""

    max_concurrent_providers: int = 5
    max_concurrent_sports_per_provider: int = 3
    max_browser_instances: int = 4  # Global browser limit for pool manager
    provider_timeout: int = 3600  # 1 hour — generous but prevents zombie connections killing the proxy tunnel
    sport_timeout: int = 60
    batch_commit_size: int = 100

    # Provider group configurations for type-aware scheduling
    provider_groups: list[ProviderGroupConfig] = Field(default_factory=list)

    # Enhancement configurations
    retry: RetryConfig = Field(default_factory=RetryConfig)
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    health_check: HealthCheckConfig = Field(default_factory=HealthCheckConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    progress: ProgressConfig = Field(default_factory=ProgressConfig)
    graceful_shutdown: GracefulShutdownConfig = Field(default_factory=GracefulShutdownConfig)
    fuzzy_match: FuzzyMatchConfig = Field(default_factory=FuzzyMatchConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)


# ============ Config Loader (Singleton) ============


class ConfigLoader:
    """
    Singleton configuration loader.

    Loads and validates configuration files on first access.
    Provides centralized access to sports and provider config.
    """

    _instance: Optional["ConfigLoader"] = None

    def __init__(self):
        self._sports: list[SportConfig] = []
        self._providers: dict[str, ProviderConfig] = {}
        self._sports_map: dict[str, SportConfig] = {}
        self._sport_aliases: dict[str, list[str]] = {}  # Sport key -> list of aliases
        self._loaded = False
        self.orchestrator_config: OrchestratorConfig | None = None
        self._sharp_blend: dict[str, Any] = {}

    @classmethod
    def get_instance(cls) -> "ConfigLoader":
        """Get or create singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
            cls._instance.load()
        return cls._instance

    def load(self, config_dir: Path | None = None):
        """
        Load configuration from YAML/JSON files.

        Args:
            config_dir: Optional config directory (defaults to src/config)
        """
        if self._loaded:
            return

        if config_dir is None:
            from ..paths import get_config_dir

            config_dir = get_config_dir()

        try:
            # Load sports config
            sports_path = config_dir / "sports.yaml"
            self._load_sports(sports_path)

            # Load providers config
            providers_path = config_dir / "providers.yaml"
            self._load_providers(providers_path)

            self._loaded = True
            logger.info(f"Configuration loaded: {len(self._sports)} sports, {len(self._providers)} providers")

        except Exception as e:
            logger.error(f"Failed to load configuration: {e}", exc_info=True)
            raise

    def _load_sports(self, sports_path: Path):
        """Load and validate sports configuration from YAML."""
        if not sports_path.exists():
            logger.warning(f"Sports config not found: {sports_path}")
            return

        with open(sports_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)

        sports_data = config.get("sports", {})

        for sport_key, sport_config in sports_data.items():
            # Extract aliases
            self._sport_aliases[sport_key] = [a.lower() for a in sport_config.get("aliases", [])]

            # Create SportConfig
            self._sports.append(
                SportConfig(
                    key=sport_key,
                    name=sport_config.get("name", sport_key),
                    kambi_sport=sport_config.get("kambi_sport", sport_key),
                    pinnacle_sport_id=sport_config.get("pinnacle_id"),
                )
            )

        # Build lookup map by key
        self._sports_map = {s.key: s for s in self._sports}

        logger.info(f"Loaded {len(self._sports)} sports from {sports_path}")

    def _load_providers(self, providers_path: Path):
        """Load and validate providers configuration."""
        if not providers_path.exists():
            logger.warning(f"Providers config not found: {providers_path}")
            return

        with open(providers_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)

        # Multi-book sharp blend config (see analysis/sharp_blend.py). Stored raw
        # — validation is light because keys are sport-dependent. Defaults to an
        # empty/Pinnacle-only blend if the block is missing.
        self._sharp_blend = config.get("sharp_blend", {}) or {}

        # Load orchestrator config
        if "orchestrator" in config:
            try:
                self.orchestrator_config = OrchestratorConfig(**config["orchestrator"])
                logger.info(
                    f"Loaded orchestrator config: max_concurrent_providers={self.orchestrator_config.max_concurrent_providers}"
                )
            except Exception as e:
                logger.error(f"Invalid orchestrator config: {e}")
                self.orchestrator_config = OrchestratorConfig()  # Use defaults
        else:
            self.orchestrator_config = OrchestratorConfig()  # Use defaults
            logger.info("No orchestrator config found, using defaults")

        # Get active providers list
        active_providers = set(config.get("active", []))

        # Load provider definitions
        providers_config = config.get("providers", {})

        for provider_id, provider_data in providers_config.items():
            # Only load active providers
            if provider_id in active_providers:
                provider_data["id"] = provider_id
                try:
                    self._providers[provider_id] = ProviderConfig(**provider_data)
                except Exception as e:
                    logger.error(f"Invalid config for provider '{provider_id}': {e}")

        logger.info(f"Loaded {len(self._providers)} active providers from {providers_path}")

    @property
    def sports(self) -> list[SportConfig]:
        """Get all sports configurations."""
        return self._sports

    @property
    def providers(self) -> dict[str, ProviderConfig]:
        """Get all provider configurations."""
        return self._providers

    def get_sport(self, key: str) -> SportConfig | None:
        """Get sport config by key."""
        return self._sports_map.get(key)

    def get_provider(self, provider_id: str) -> ProviderConfig | None:
        """Get provider config by ID."""
        return self._providers.get(provider_id)

    def get_enabled_providers(self) -> list[str]:
        """Get list of enabled provider IDs."""
        return list(self._providers.keys())

    def get_orchestrator_config(self) -> OrchestratorConfig:
        """Get orchestrator configuration."""
        if self.orchestrator_config is None:
            raise ValueError("Configuration not loaded")
        return self.orchestrator_config

    def get_sharp_blend(self) -> dict[str, Any]:
        """Return the raw sharp_blend config block (empty dict if unset)."""
        return self._sharp_blend

    def get_sport_aliases(self, sport_key: str) -> list[str]:
        """Get aliases for a sport (e.g., football -> ['soccer', 'fotboll'])."""
        return self._sport_aliases.get(sport_key.lower(), [])


# ============ Convenience Functions ============


def load_config() -> ConfigLoader:
    """Load and return configuration singleton."""
    return ConfigLoader.get_instance()


def get_exchange_rate(provider_id: str) -> float:
    """Get exchange rate to SEK for a provider. Returns 1.0 for SEK providers."""
    config = load_config()
    provider = config.get_provider(provider_id)
    if provider and provider.exchange_rate_sek:
        return provider.exchange_rate_sek
    return 1.0


def get_provider_currency(provider_id: str) -> str:
    """Get the native currency for a provider."""
    config = load_config()
    provider = config.get_provider(provider_id)
    if provider and provider.currency:
        return provider.currency
    return "SEK"


def get_all_exchange_rates() -> dict[str, float]:
    """Get exchange rates for all providers (only non-SEK providers included)."""
    config = load_config()
    rates = {}
    for pid, pconfig in config.providers.items():
        if pconfig.currency and pconfig.currency != "SEK":
            rates[pid] = pconfig.exchange_rate_sek or 1.0
    return rates
