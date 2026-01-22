"""
Configuration Loader

Centralized configuration loading with validation.
Loads providers.yaml and sports.json with schema validation.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


# ============ Pydantic Models for Validation ============

class SportConfig(BaseModel):
    """Configuration for a sport/league."""
    name: str
    kambi_sport: str
    polymarket_series_id: Optional[int] = None
    polymarket_slug: Optional[str] = None
    polymarket_tag_id: Optional[int] = None

    @property
    def polymarket_config(self) -> Optional[Dict]:
        """Get Polymarket-specific config."""
        if self.polymarket_series_id or self.polymarket_slug or self.polymarket_tag_id:
            return {
                "id": self.polymarket_series_id,
                "slug": self.polymarket_slug,
                "tag_id": self.polymarket_tag_id,
            }
        return None


class ProviderConfig(BaseModel):
    """Configuration for a betting provider."""
    id: str
    name: Optional[str] = None
    retriever_type: str
    domain: Optional[str] = None
    api_base: Optional[str] = None
    base_url: Optional[str] = None
    brand: Optional[str] = None
    params: Dict = Field(default_factory=dict)


class AppConfig(BaseModel):
    """Root application configuration."""
    sports: List[SportConfig]
    providers: Dict[str, ProviderConfig]
    active_providers: List[str] = Field(default_factory=list)

    @field_validator('providers', mode='before')
    def add_provider_ids(cls, v):
        """Ensure provider IDs are set."""
        if isinstance(v, dict):
            for pid, config in v.items():
                if isinstance(config, dict):
                    config['id'] = pid
        return v


# ============ Config Loader (Singleton) ============

class ConfigLoader:
    """
    Singleton configuration loader.

    Loads and validates configuration files on first access.
    Provides centralized access to sports and provider config.
    """
    _instance: Optional['ConfigLoader'] = None

    def __init__(self):
        self._sports: List[SportConfig] = []
        self._providers: Dict[str, ProviderConfig] = {}
        self._sports_map: Dict[str, SportConfig] = {}
        self._loaded = False

    @classmethod
    def get_instance(cls) -> 'ConfigLoader':
        """Get or create singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
            cls._instance.load()
        return cls._instance

    def load(self, config_dir: Optional[Path] = None):
        """
        Load configuration from YAML/JSON files.

        Args:
            config_dir: Optional config directory (defaults to src/config)
        """
        if self._loaded:
            return

        if config_dir is None:
            config_dir = Path(__file__).parent

        try:
            # Load sports config
            sports_path = config_dir / "sports.json"
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
        """Load and validate sports configuration."""
        if not sports_path.exists():
            logger.warning(f"Sports config not found: {sports_path}")
            return

        with open(sports_path, "r", encoding="utf-8") as f:
            sports_data = json.load(f)

        # Handle nested format with leagues
        sport_dicts = []
        if isinstance(sports_data, list) and sports_data and "leagues" in sports_data[0]:
            for group in sports_data:
                defaults = group.get("defaults", {})
                for league in group.get("leagues", []):
                    merged = {**defaults, **league}
                    sport_dicts.append(merged)
        else:
            # Flat format
            sport_dicts = sports_data

        # Validate with Pydantic
        self._sports = [SportConfig(**s) for s in sport_dicts]

        # Build map for quick lookup
        self._sports_map = {s.name: s for s in self._sports}

        logger.info(f"Loaded {len(self._sports)} sports from {sports_path}")

    def _load_providers(self, providers_path: Path):
        """Load and validate providers configuration."""
        if not providers_path.exists():
            logger.warning(f"Providers config not found: {providers_path}")
            return

        with open(providers_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

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
    def sports(self) -> List[SportConfig]:
        """Get all sports configurations."""
        return self._sports

    @property
    def providers(self) -> Dict[str, ProviderConfig]:
        """Get all provider configurations."""
        return self._providers

    def get_sport(self, name: str) -> Optional[SportConfig]:
        """Get sport config by name."""
        return self._sports_map.get(name)

    def get_provider(self, provider_id: str) -> Optional[ProviderConfig]:
        """Get provider config by ID."""
        return self._providers.get(provider_id)

    def get_enabled_providers(self) -> List[str]:
        """Get list of enabled provider IDs."""
        return list(self._providers.keys())

    def get_sports_map_for_polymarket(self) -> Dict[str, Dict]:
        """
        Get sports mapping for Polymarket.

        Returns:
            Dictionary mapping sport name to Polymarket config
        """
        mapping = {}
        for sport in self._sports:
            if sport.polymarket_config:
                mapping[sport.name] = sport.polymarket_config
        return mapping


# ============ Convenience Function ============

def load_config() -> ConfigLoader:
    """Load and return configuration singleton."""
    return ConfigLoader.get_instance()
