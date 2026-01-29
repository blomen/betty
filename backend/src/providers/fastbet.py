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
    """
    Retriever for Fastbet sportsbook.

    Note: Despite being owned by Bethard Group, Fastbet uses SpringBuilder/YoSpace
    technology with a different API structure than standard SBTech.
    """

    # Fastbet sport slugs
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

    # Fastbet uses SpringBuilder API, not standard SBTech patterns
    API_PATTERNS = [
        '/prematch/match/',  # SpringBuilder prematch API
        '/live/match/',      # SpringBuilder live API (if needed)
    ]

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        super().__init__(config, transport)

    def _get_sport_url(self, sport: str) -> str:
        """Get Fastbet sportsbook URL for a sport."""
        sport_slug = self.SPORT_SLUGS.get(sport, sport)
        # Fastbet uses /sv/sports/ path (Swedish site)
        return f"{self.site_url}/sv/sports/{sport_slug}"
