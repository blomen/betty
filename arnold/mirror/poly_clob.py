"""Shared Polymarket CLOB pricing — single source of truth for live odds.

Both the 30s live-odds poller (`poly_live_poller`) and the user-navigation
live check (`workflows/strategies/polymarket._check_live_price`) must price a
Polymarket outcome the same way: the executable best **ask** from the CLOB
order book — not Gamma's mid/last `outcomePrices` — keyed on the outcome's
`token_id`, with the same 2% fee haircut the server extractor applies.

Keeping the math here stops the two paths from drifting. They did: the poller
shipped a mid-price, no-fee, team-name-matched copy that broadcast wrong (and
sometimes swapped) odds over the top of everything every 30s.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_CLOB_PRICE_URL = "https://clob.polymarket.com/price"
# Mirrors PolymarketRetriever.POLY_FEE_RATE — Polymarket charges ~2% on net
# winnings. Live odds apply the same haircut as extraction-time odds so the
# edge column doesn't jump when a live value overrides the row.
POLY_FEE_RATE = 0.02


async def fetch_clob_ask(token_id: str) -> float | None:
    """Best ask — the executable buy price — for a Polymarket outcome token.

    CLOB's /price `side` is the resting ORDER side, not the taker's intent:
    side=sell returns the lowest sell offer, i.e. what a buyer actually pays.
    side=buy returns the best bid. Verified empirically against Gamma bestAsk.

    Returns None when the token is unknown or the API is unreachable.
    """
    if not token_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=4.0)) as client:
            resp = await client.get(_CLOB_PRICE_URL, params={"token_id": token_id, "side": "sell"})
            resp.raise_for_status()
            price = float((resp.json() or {}).get("price") or 0.0)
    except (httpx.HTTPError, ValueError, TypeError) as e:
        logger.debug("polymarket CLOB /price failed for token %s…: %r", str(token_id)[:12], e)
        return None
    return price if 0.0 < price < 1.0 else None


def ask_to_odds(ask: float) -> float:
    """Fee-adjusted decimal odds from a CLOB ask price.

    Matches backend PolymarketRetriever._price_to_odds: net winnings are
    haircut by POLY_FEE_RATE, so effective_odds = 1 + (1/ask - 1)*(1 - fee).
    """
    raw_odds = 1.0 / ask
    return round(1.0 + (raw_odds - 1.0) * (1.0 - POLY_FEE_RATE), 3)
