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
                
                # Check format and flatten if nested
                self.sports = []
                if isinstance(sports_data, list) and len(sports_data) > 0 and "leagues" in sports_data[0]:
                    # New format: List of Sport Groups
                    for group in sports_data:
                        defaults = group.get("defaults", {})
                        for league in group.get("leagues", []):
                            # Merge defaults with league data
                            merged = defaults.copy()
                            merged.update(league)
                            self.sports.append(SportConfig(merged))
                else:
                    # Old format: Flat list
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
        # Check if we already have an instance
        if hasattr(self, "_extractor_cache") and provider_id in self._extractor_cache:
            return self._extractor_cache[provider_id]
            
        if not hasattr(self, "_extractor_cache"):
            self._extractor_cache = {}

        config = self.providers.get(provider_id)
        if not config:
            raise ValueError(f"Provider {provider_id} not found or not active.")
            
        retriever_type = config.get("retriever_type")
        
        # Mapping
        # Fallback for old configs if any remains
        api_type = config.get("api_type")
        retriever = None
        
        if retriever_type == "kambi":
            retriever = KambiRetriever(config)
        elif retriever_type == "polymarket":
            retriever = PolymarketRetriever(config)
        elif retriever_type == "spectate":
            retriever = SpectateRetriever(config)
        
        elif api_type == "kambi": retriever = KambiRetriever(config)
        elif api_type == "polymarket": retriever = PolymarketRetriever(config)
        
        else:
            dom_type = config.get("dom_type")
            if dom_type == "spectate": retriever = SpectateRetriever(config)
        
        if retriever_type == "snabbare":
            # For Snabbare we use local import or ensure top level import
            from backend.src.providers.snabbare import SnabbareRetriever
            retriever = SnabbareRetriever(config)
        
        if not retriever:
            raise ValueError(f"Unknown retriever type for {provider_id}")
            
        self._extractor_cache[provider_id] = retriever
        return retriever
