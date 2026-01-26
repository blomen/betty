"""
Fastbet Retriever - SBTech-powered sportsbook

Fastbet (fastbet.com) is a Swedish-licensed Pay N Play bookmaker using SBTech platform.
Owned by Bethard Group Limited (same parent as Bethard).
Uses the shared SBTech base retriever with brand-specific configuration.
"""

from typing import Dict, Any, Optional
from .sbtech_base import SBTechRetriever
from ..core import BrowserTransport


class FastbetRetriever(SBTechRetriever):
    """Retriever for Fastbet sportsbook."""

    # Fastbet sport slugs (same as Bethard - same platform/company)
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
        """Get Fastbet sportsbook URL for a sport."""
        sport_slug = self.SPORT_SLUGS.get(sport, sport)
        # URL structure should match Bethard (same company/platform)
        return f"{self.site_url}/sports/{sport_slug}"
