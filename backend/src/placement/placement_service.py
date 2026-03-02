"""
Placement Service — URL navigation + CDP bet slip filling.

Returns URLs for the frontend to open in named browser windows.
Each provider gets its own window name so the same tab is reused.
Also supports CDP-based bet slip filling via fill_slip().
"""

import logging

from .url_builder import build_match_url, build_deposit_url, build_my_bets_url, build_results_url
from .slip_filler import SlipFillerService, SlipRequest

logger = logging.getLogger(__name__)


class PlacementService:
    """Provides URL-based navigation and CDP slip filling."""

    def __init__(self):
        self._slip_filler: SlipFillerService | None = None

    def _get_slip_filler(self) -> SlipFillerService:
        """Lazy-init the slip filler with registered strategies."""
        if self._slip_filler is None:
            from .strategies import KambiSlipStrategy

            self._slip_filler = SlipFillerService()
            self._slip_filler.register_strategy("kambi", KambiSlipStrategy())
            # Phase 2: register AltenarSlipStrategy, OBGSlipStrategy, etc.
        return self._slip_filler

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

    async def navigate_to_my_bets(self, provider_id: str) -> dict:
        """Build a URL for a provider's my bets / bet history page."""
        url = await build_my_bets_url(provider_id)
        return {
            "url": url,
            "provider_id": provider_id,
            "window_name": f"bbq_{provider_id}",
        }

    async def navigate_to_results(self, provider_id: str) -> dict:
        """Build a URL for a provider's results/scores page."""
        url = await build_results_url(provider_id)
        return {
            "url": url,
            "provider_id": provider_id,
            "window_name": f"bbq_{provider_id}",
        }

    async def fill_slip(self, **kwargs) -> dict:
        """Navigate to provider and auto-fill bet slip via CDP.

        Returns status: "ready" (slip filled), "navigated_only" (page open),
        or "error" (CDP/navigation failure).
        """
        filler = self._get_slip_filler()
        request = SlipRequest(
            provider_id=kwargs.get("provider_id", ""),
            event_id=kwargs.get("event_id", ""),
            market=kwargs.get("market", ""),
            outcome=kwargs.get("outcome", ""),
            point=kwargs.get("point"),
            stake=kwargs.get("stake", 0),
            expected_odds=kwargs.get("expected_odds", 0),
            provider_meta=kwargs.get("provider_meta"),
            home_team=kwargs.get("home_team", ""),
            away_team=kwargs.get("away_team", ""),
        )
        result = await filler.fill_slip(request)
        resp = {
            "status": result.status.value,
            "message": result.message,
            "provider_id": result.provider_id,
            "url": result.url,
            "actual_odds": result.actual_odds,
        }
        # Include post-login sync data (Polymarket wallet-based)
        if result.balance is not None:
            resp["balance"] = result.balance
        if result.wallet_address:
            resp["wallet_address"] = result.wallet_address
        if result.balance_updated:
            resp["balance_updated"] = True
        return resp
