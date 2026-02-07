"""
Bethard Retriever - SBTech-powered sportsbook

Bethard is a Malta-licensed bookmaker using SBTech platform.
Uses the shared SBTech base retriever with brand-specific configuration.

URL structure: /sv/sports/<swedish-slug> (Swedish locale)
"""

from typing import Dict, Any, Optional
from .sbtech_base import SBTechRetriever
from ..core import BrowserTransport


class BethardRetriever(SBTechRetriever):
    """Retriever for Bethard sportsbook."""

    # Bethard uses Swedish sport slugs
    SPORT_SLUGS: Dict[str, str] = {
        "football": "fotboll",
        "basketball": "basket",
        "tennis": "tennis",
        "ice_hockey": "ishockey",
        "american_football": "amerikansk-fotboll",
        "baseball": "baseboll",
        "mma": "mma",
        "esports": "esports",
    }

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        super().__init__(config, transport)

    def _get_sport_url(self, sport: str) -> str:
        """Get Bethard sportsbook URL for a sport (Swedish locale).

        Appends ?tab=upcoming to bypass the default "Featured" tab which
        only shows a handful of promoted events.
        """
        sport_slug = self.SPORT_SLUGS.get(sport, sport)
        return f"{self.site_url}/sv/sports/{sport_slug}?tab=upcoming"

    def _get_event_detail_url(self, slug: str) -> str:
        """Get Bethard event detail URL (Swedish locale)."""
        return f"{self.site_url}/sv/sports/{slug}"
