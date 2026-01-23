"""
ComeOn Retriever - SBTech-powered sportsbook

ComeOn is part of ComeOn Group, long-standing SBTech partner since 2009.
Uses the shared SBTech base retriever with brand-specific configuration.
"""

from typing import Dict, Any, Optional
from .sbtech_base import SBTechRetriever
from ..core import BrowserTransport


class ComeOnRetriever(SBTechRetriever):
    """Retriever for ComeOn sportsbook."""

    # ComeOn-specific sport slugs
    SPORT_SLUGS: Dict[str, Any] = {
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
        """Get ComeOn sportsbook URL for a sport."""
        sport_slug = self.SPORT_SLUGS.get(sport, sport)
        # ComeOn uses /sportsbook/ path
        return f"{self.site_url}/sportsbook/{sport_slug}"
