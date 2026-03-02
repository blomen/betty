"""
PolymarketAccountStrategy — API-based account sync for Polymarket.

Uses the public Data API (data-api.polymarket.com) to fetch portfolio
positions and closed positions. No CDP/browser needed — only a wallet address.

Converts closed positions into ScrapedBet format so AccountSyncService's
existing matching/settlement logic works unchanged.
"""

import logging

from .kambi_account import AccountSyncResult, ScrapedBet
from ...services.polymarket_client import PolymarketDataClient

logger = logging.getLogger(__name__)


class PolymarketAccountStrategy:
    """API-based account sync for Polymarket — no browser needed."""

    requires_browser = False  # AccountSyncService skips CDP for this strategy

    def __init__(self, wallet_address: str):
        self.wallet_address = wallet_address
        self.client = PolymarketDataClient()

    async def sync(self, page=None, provider_id: str = "polymarket") -> AccountSyncResult:
        """
        Sync Polymarket account via public Data API.

        The `page` parameter is accepted for interface compatibility with
        AccountSyncService but is ignored — no browser needed.
        """
        result = AccountSyncResult(provider_id=provider_id)

        if not self.wallet_address:
            result.error = "No wallet address configured"
            return result

        try:
            portfolio = await self.client.get_portfolio(self.wallet_address)
        except Exception as e:
            result.error = f"Data API error: {e}"
            logger.warning(f"[PolymarketSync] API error: {e}")
            return result

        # Balance = total portfolio value (sum of open position current values)
        result.balance = portfolio.total_value_usdc

        # Convert closed positions to ScrapedBet format for settlement matching.
        # AccountSyncService._find_best_match() uses odds/stake/event_text scoring.
        for closed in portfolio.closed_positions:
            if closed.total_bought <= 0:
                continue

            # Determine result from realized P&L
            if closed.realized_pnl > 0.01:
                bet_result = "won"
            elif closed.realized_pnl < -0.01:
                bet_result = "lost"
            else:
                bet_result = "void"  # Break-even or dust

            # Reconstruct odds from avg_price: odds = 1 / avg_price
            odds = round(1.0 / closed.avg_price, 3) if closed.avg_price > 0 else 0.0

            # Payout = initial investment + realized P&L
            payout = max(0, closed.total_bought * closed.avg_price + closed.realized_pnl)

            result.scraped_bets.append(ScrapedBet(
                result=bet_result,
                stake=round(closed.total_bought * closed.avg_price, 2),  # USDC spent
                payout=round(payout, 2),
                odds=odds,
                is_freebet=False,
                event_text=closed.title,
                coupon_id=closed.condition_id,  # Links to Odds.clob_token_id
            ))

        logger.info(
            f"[PolymarketSync] Fetched portfolio: "
            f"${portfolio.total_value_usdc:.2f} value, "
            f"{portfolio.open_count} open, "
            f"{len(result.scraped_bets)} closed positions"
        )

        return result
