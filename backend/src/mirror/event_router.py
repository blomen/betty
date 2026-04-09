"""EventRouter — classify intercepted browser responses and route to persist + broadcast."""

import logging
from typing import Any

logger = logging.getLogger(__name__)

_BALANCE_PATTERNS = (
    "/account/balance", "/mainbalance", "/wallets", "/wallet/balance",
    "/api/sb/v2/balance", "/cashier/balance",
)
_HISTORY_PATTERNS = (
    "/widgetBetHistory", "/bethistory", "/betHistory", "/bets?status=",
    "/bet-history", "/myBets", "/portfolio?tab=history",
)
_BET_PLACEMENT_PATTERNS = (
    "/placeWidget", "/placeBet", "/coupons", "/bets/straight",
    "/bets/parlay", "/bets/place", "clob.polymarket.com/order",
)
_ODDS_PATTERNS = (
    "/GetEventDetails", "/events-table", "/event/", "/odds/",
    "/offering/v2018/", "/market/",
)
_NOTIFICATION_PATTERNS = (
    "/notification", "/preferences", "/communication", "/consent",
    "/marketing", "/subscription",
)


class EventRouter:
    """Classifies intercepted browser responses by URL pattern and routes them to persist + broadcast."""

    def __init__(self, session_factory=None):
        self._session_factory = session_factory

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def classify(self, url: str, response_body: Any) -> str | None:
        """Return category string or None if the URL doesn't match any known pattern."""
        for pattern in _BALANCE_PATTERNS:
            if pattern in url:
                return "balance"
        for pattern in _BET_PLACEMENT_PATTERNS:
            if pattern in url:
                return "bet_confirm"
        for pattern in _HISTORY_PATTERNS:
            if pattern in url:
                return "history"
        for pattern in _ODDS_PATTERNS:
            if pattern in url:
                return "odds"
        for pattern in _NOTIFICATION_PATTERNS:
            if pattern in url:
                return "notification"
        return None

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    async def route(
        self,
        provider_id: str,
        category: str,
        url: str,
        response_body: Any,
        request_body: Any = None,
        page_url: str | None = None,
    ) -> None:
        """Persist to DB then broadcast to the appropriate SSE channel."""
        try:
            if category == "balance":
                payload = await self._persist_balance(provider_id, response_body)
                if payload:
                    await self._broadcast("sync", "balance_update", payload)
            elif category == "history":
                payload = await self._persist_history(provider_id, response_body)
                if payload:
                    await self._broadcast("sync", "history_received", payload)
            elif category == "bet_confirm":
                payload = {"provider_id": provider_id, "url": url}
                await self._broadcast("action", "bet_confirm", payload)
            elif category == "odds":
                payload = await self._persist_prices(provider_id, response_body)
                if payload:
                    await self._broadcast("price", "odds_update", payload)
            elif category == "notification":
                payload = {"provider_id": provider_id, "url": url}
                await self._broadcast("sync", "notification", payload)
        except Exception:
            logger.exception("EventRouter.route failed for provider=%s category=%s url=%s", provider_id, category, url)

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    async def _persist_balance(self, provider_id: str, body: Any) -> dict | None:
        """Extract balance amount from various response shapes and persist a BalanceLog row."""
        if not isinstance(body, dict):
            return None

        amount: float | None = None

        for key in ("balance", "amount", "availableBalance", "cash", "total"):
            if key in body:
                val = body[key]
                if isinstance(val, (int, float)):
                    amount = float(val)
                    break
                elif isinstance(val, dict) and "amount" in val:
                    amount = float(val["amount"])
                    break

        # Gecko V2 fallback: body["wallets"][0]["balance"]
        if amount is None:
            try:
                wallets = body.get("wallets")
                if wallets and isinstance(wallets, list) and len(wallets) > 0:
                    amount = float(wallets[0]["balance"])
            except (KeyError, TypeError, ValueError):
                pass

        if amount is None:
            return None

        if self._session_factory is not None:
            try:
                from ..db.models import BalanceLog
                with self._session_factory() as session:
                    log = BalanceLog(
                        provider_id=provider_id,
                        amount=amount,
                        source="intercepted",
                    )
                    session.add(log)
                    session.commit()
            except Exception:
                logger.exception("Failed to persist BalanceLog for provider=%s", provider_id)

        return {"provider_id": provider_id, "amount": amount}

    async def _persist_history(self, provider_id: str, body: Any) -> dict | None:
        """Signal that bet history was received. Provider-specific parsing stays in MirrorService."""
        return {"provider_id": provider_id, "received": True}

    async def _persist_prices(self, provider_id: str, body: Any) -> dict | None:
        """Signal that odds data was received. Will be extended per-provider later."""
        return {"provider_id": provider_id, "received": True}

    # ------------------------------------------------------------------
    # Broadcast helpers
    # ------------------------------------------------------------------

    async def _broadcast(self, channel: str, event_type: str, data: dict) -> None:
        """Route to the correct SSE channel."""
        from .channels import sync_channel, price_channel, action_channel

        broadcaster = {
            "sync": sync_channel,
            "price": price_channel,
            "action": action_channel,
        }.get(channel)

        if broadcaster is None:
            logger.warning("EventRouter: unknown channel %r", channel)
            return

        broadcaster.publish(event_type, data)

    # ------------------------------------------------------------------
    # Public broadcast methods (for MirrorService)
    # ------------------------------------------------------------------

    async def broadcast_action(self, event_type: str, data: dict) -> None:
        """Broadcast an action-lane event."""
        await self._broadcast("action", event_type, data)

    async def broadcast_sync(self, event_type: str, data: dict) -> None:
        """Broadcast a sync-lane event."""
        await self._broadcast("sync", event_type, data)

    async def broadcast_price(self, event_type: str, data: dict) -> None:
        """Broadcast a price-lane event."""
        await self._broadcast("price", event_type, data)
