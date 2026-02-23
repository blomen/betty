"""
Placement Service — URL navigation for provider sites.

No Chrome/CDP session management. Users authenticate via BankID in their own browser.
This service just builds URLs and returns them for the frontend to open.
"""

import logging
from typing import Optional

from .base import PlacementRequest, PlacementResult, PlacementStatus

logger = logging.getLogger(__name__)


class PlacementService:
    """
    Provides URL-based navigation to provider sites.

    No Chrome sessions — users log in via BankID themselves.
    """

    async def navigate_to_event(self, **kwargs) -> dict:
        """Build a URL for a provider event and return it."""
        from .url_builder import build_match_url

        url = await build_match_url(
            provider_id=kwargs.get("provider_id", ""),
            provider_meta=kwargs.get("provider_meta"),
            home_team=kwargs.get("home_team", ""),
            away_team=kwargs.get("away_team", ""),
            event_id=kwargs.get("event_id", ""),
        )
        return {"navigated": False, "url": url, "method": "url", "provider_id": kwargs.get("provider_id", "")}
