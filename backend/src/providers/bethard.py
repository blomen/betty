"""
Bethard Retriever - SBTech-powered sportsbook

Bethard is a Malta-licensed bookmaker using SBTech platform.
Uses the shared SBTech base retriever with brand-specific configuration.
"""

from typing import Dict, Any, Optional
from .sbtech_base import SBTechRetriever
from ..core import BrowserTransport


class BethardRetriever(SBTechRetriever):
    """Retriever for Bethard sportsbook."""

    # Bethard-specific sport slugs (update after inspecting site)
    SPORT_SLUGS: Dict[str, str] = {
        "football": "football",
        "basketball": "basketball",
        "tennis": "tennis",
        "ice_hockey": "ice-hockey",
        "american_football": "american-football",
        "baseball": "baseball",
        "mma": "mma",
        "esports": "esports",
    }

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        super().__init__(config, transport)

    def _get_sport_url(self, sport: str) -> str:
        """Get Bethard sportsbook URL for a sport."""
        sport_slug = self.SPORT_SLUGS.get(sport, sport)
        # URL structure to be confirmed during testing
        return f"{self.site_url}/sports/{sport_slug}"
