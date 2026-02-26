"""
Placement Service — URL navigation for provider sites.

Returns URLs for the frontend to open in named browser windows.
Each provider gets its own window name so the same tab is reused.
"""

import logging

from .url_builder import build_match_url, build_deposit_url

logger = logging.getLogger(__name__)


class PlacementService:
    """Provides URL-based navigation to provider sites."""

    async def navigate_to_event(self, **kwargs) -> dict:
        """Build a URL for a provider event page."""
        provider_id = kwargs.get("provider_id", "")
        url = await build_match_url(
            provider_id=provider_id,
            provider_meta=kwargs.get("provider_meta"),
            home_team=kwargs.get("home_team", ""),
            away_team=kwargs.get("away_team", ""),
            event_id=kwargs.get("event_id", ""),
        )
        return {
            "url": url,
            "provider_id": provider_id,
            "window_name": f"bbq_{provider_id}",
        }

    async def navigate_to_deposit(self, provider_id: str) -> dict:
        """Build a URL for a provider deposit/cashier page."""
        url = await build_deposit_url(provider_id)
        return {
            "url": url,
            "provider_id": provider_id,
            "window_name": f"bbq_{provider_id}",
        }
