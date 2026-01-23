"""
Hajper Retriever - SBTech-powered sportsbook

Hajper is launched by ComeOn Group specifically for Swedish market.
Uses the shared SBTech base retriever with brand-specific configuration.
"""

from typing import Dict, Any, Optional
from .sbtech_base import SBTechRetriever
from ..core import BrowserTransport


class HajperRetriever(SBTechRetriever):
    """Retriever for Hajper sportsbook."""

    # Hajper-specific sport IDs (uses numeric IDs + Swedish names)
    SPORT_SLUGS: Dict[str, str] = {
        "football": "1-fotboll",
        "basketball": "2-basket",
        "tennis": "3-tennis",
        "ice_hockey": "4-ishockey",
        "american_football": "5-amerikansk-fotboll",
        "baseball": "6-baseboll",
        "mma": "7-mma",
        "esports": "8-esport",
    }

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        super().__init__(config, transport)

    def _get_sport_url(self, sport: str) -> str:
        """Get Hajper sportsbook URL for a sport."""
        sport_slug = self.SPORT_SLUGS.get(sport, sport)
        # Hajper uses /sportsbook/sport/ path with numeric IDs and Swedish names
        return f"{self.site_url}/sportsbook/sport/{sport_slug}"
