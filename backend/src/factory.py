import json
import os
import logging
from typing import List, Dict, Type

# Core
from backend.src.core import Retriever

# Providers
from backend.src.providers.kambi import KambiRetriever
from backend.src.providers.polymarket import PolymarketRetriever
from backend.src.providers.spectate import SpectateRetriever


logger = logging.getLogger(__name__)

class SportConfig:
    def __init__(self, data):
        self.name = data.get("name")
        self.kambi_sport = data.get("kambi_sport")
        self.polymarket_series_id = data.get("polymarket_series_id")
        self.data = data

class ExtractorFactory:
    _instance = None
    
    def __init__(self):
        self.providers = {} # id -> config
        self.sports = []
        self._load_configs()
        
    @classmethod
    def get_instance(cls):
        if not cls._instance:
            cls._instance = cls()
        return cls._instance

    def _load_configs(self):
        base_path = os.path.dirname(__file__)
        config_path = os.path.join(base_path, "config")
        
        # Load Sports List
        try:
            with open(os.path.join(config_path, "sports.json"), "r") as f:
                sports_data = json.load(f)
                self.sports = [SportConfig(s) for s in sports_data]
        except Exception as e:
            logger.warning(f"Failed to load sports.json: {e}")
            self.sports = []
        
        # Load Providers List
        try:
            with open(os.path.join(config_path, "providers.json"), "r") as f:
                active_providers = json.load(f)
        except Exception:
            active_providers = []

        # Load Provider Configs
        providers_dir = os.path.join(config_path, "providers")
        if os.path.exists(providers_dir):
            for filename in os.listdir(providers_dir):
                if not filename.endswith(".json"): continue
                
                # Check if active (files are like "unibet.json", list has "unibet.se" usually or match by ID?)
                # History says providers.json has domains.
                # But provider configs have ID = "unibet".
                # We should match based on domain or just load all?
                # User said "only use providers.json as providers".
                # So we must check if domain/id is in the list.
                
                try:
                    with open(os.path.join(providers_dir, filename), "r") as f:
                         config = json.load(f)
                         
                    # Check activation
                    # Strategy: Config has "domain" or "id". providers.json has list of strings (domains).
                    domain = config.get("domain")
                    if domain and domain in active_providers:
                        self.providers[config["id"]] = config
                    elif config.get("id") == "polymarket": # Special case if logic differs, but Polymarket doesn't have domain in list?
                        # Polymarket often treated specially. Let's check logic.
                        # If "polymarket" is not in providers.json? 
                        # Assuming it needs to be explicitly enabled.
                        pass
                        
                    # Also load special ones like 'bovada' if explicitly in list (user added bovada.lv)
                    if config.get("id") in active_providers or str(config.get("domain")) in active_providers:
                         self.providers[config["id"]] = config

                except Exception as e:
                    logger.warning(f"Failed to load config {filename}: {e}")

    def get_enabled_providers(self) -> List[str]:
        """Get list of enabled provider IDs."""
        return list(self.providers.keys())

    def get_extractor(self, provider_id: str) -> Retriever:
        config = self.providers.get(provider_id)
        if not config:
            raise ValueError(f"Provider {provider_id} not found or not active.")
            
        retriever_type = config.get("retriever_type")
        
        # Mapping
        if retriever_type == "kambi":
            return KambiRetriever(config)
        elif retriever_type == "polymarket":
            return PolymarketRetriever(config)
        elif retriever_type == "spectate":
            return SpectateRetriever(config)

            
        # Fallback for old configs if any remains
        api_type = config.get("api_type")
        if api_type == "kambi": return KambiRetriever(config)
        if api_type == "polymarket": return PolymarketRetriever(config)
        
        dom_type = config.get("dom_type")
        if dom_type == "spectate": return SpectateRetriever(config)
        
        raise ValueError(f"Unknown retriever type for {provider_id}")
