"""
10Bet Retriever - SBTech-powered sportsbook

10Bet is a Swedish-licensed bookmaker using the SBTech platform.
Uses the shared SBTech base retriever with brand-specific configuration.

URL structure: /sports/<sport>/competitions (English slugs with underscores)
"""

from typing import Dict, Any, Optional
from .sbtech_base import SBTechRetriever
from ..core import BrowserTransport


class TenBetRetriever(SBTechRetriever):
    """Retriever for 10Bet sportsbook."""

    SPORT_SLUGS: Dict[str, str] = {
        "football": "football",
        "basketball": "basketball",
        "tennis": "tennis",
        "ice_hockey": "ice_hockey",
        "american_football": "american_football",
        "baseball": "baseball",
        "mma": "mma",
        "esports": "esports",
    }

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        super().__init__(config, transport)

    def _get_sport_url(self, sport: str) -> str:
        """Get 10Bet sportsbook URL for a sport."""
        sport_slug = self.SPORT_SLUGS.get(sport, sport)
        return f"{self.site_url}/sports/{sport_slug}/competitions"
