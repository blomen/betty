"""Kalshi prediction-market extractor.

Pulls binary YES/NO contracts from Kalshi's public REST API and converts
them to StandardEvent moneyline / 1x2 / spread / total markets. Extraction
is unauthenticated — only placement (in the mirror workflow) needs API keys.
"""
from __future__ import annotations

import logging

from ..core import StandardEvent

logger = logging.getLogger(__name__)

# Ticker-prefix → canonical sport. Extend as new series appear.
KALSHI_SERIES_TO_SPORT: dict[str, str] = {
    "KXNBAGAME": "basketball",
    "KXNCAABGAME": "basketball",
    "KXNFLGAME": "american_football",
    "KXNCAAFGAME": "american_football",
    "KXMLBGAME": "baseball",
    "KXNHLGAME": "ice_hockey",
    "KXTENNIS": "tennis",
    "KXUFC": "mma",
    "KXBOXING": "boxing",
    "KXEPL": "football",
    "KXUCL": "football",
    "KXWC": "football",
}

# Sports with no draw outcome → 2-way moneyline; others → 3-way 1x2.
_NO_DRAW_SPORTS = frozenset(
    {
        "basketball",
        "american_football",
        "baseball",
        "ice_hockey",
        "tennis",
        "mma",
        "boxing",
    }
)


def series_to_sport(ticker: str) -> str | None:
    """Resolve a Kalshi event/market ticker to our canonical sport name.

    Uses longest-prefix match so more specific prefixes win (e.g. KXNCAAB
    before KX).
    """
    for prefix in sorted(KALSHI_SERIES_TO_SPORT.keys(), key=len, reverse=True):
        if ticker.startswith(prefix):
            return KALSHI_SERIES_TO_SPORT[prefix]
    return None


def _price_to_odds(price: float, fee_rate: float) -> float:
    """Convert a YES-contract price ($0–$1) to decimal odds with fee adjustment.

    Kalshi's per-trade fee is applied as an incremental cost on the entry price.
    effective_price = price + fee_rate * price * (1 - price)
    decimal_odds = 1 / effective_price
    """
    effective = price + fee_rate * price * (1.0 - price)
    if effective <= 0.0:
        return 0.0
    return round(1.0 / effective, 4)


def _market_price_dollars(m: dict) -> float:
    """Kalshi returns yes_ask_dollars as a float 0–1 (already in dollars).

    Live schema uses string values ("0.7700"), tests use floats — cast both.
    """
    val = m.get("yes_ask_dollars")
    if val is None or val == "":
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _market_volume_usd(m: dict) -> float:
    """Total lifetime volume in USD notional. Kalshi publishes this as volume_fp."""
    val = m.get("volume_fp", 0)
    if val is None or val == "":
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _extract_teams_from_title(title: str) -> tuple[str, str]:
    """Split 'Home vs Away' / 'Home @ Away' into (home, away). Falls back gracefully."""
    for sep in (" vs ", " @ ", " at ", " v. ", " v "):
        if sep in title:
            left, right = title.split(sep, 1)
            # Strip trailing question-marks / 'Winner?' suffixes.
            right = right.split("?")[0].strip()
            right = right.replace("Winner", "").strip()
            return left.strip(), right
    return title.strip(), ""


def parse_event(
    raw: dict,
    min_volume_usd: float = 100.0,
    fee_rate: float = 0.02,
) -> StandardEvent | None:
    """Parse one Kalshi event (container of binary markets) into a StandardEvent.

    Returns None if:
    - Series ticker not in KALSHI_SERIES_TO_SPORT
    - All markets below volume threshold
    - All prices exactly $0.50 (untraded)
    - Not enough active markets to form a valid moneyline/1x2
    """
    event_ticker = raw.get("event_ticker", "")
    sport = series_to_sport(event_ticker)
    if sport is None:
        return None

    raw_markets = [
        m
        for m in raw.get("markets", [])
        if m.get("status") == "active"
        and _market_volume_usd(m) >= min_volume_usd
    ]
    if not raw_markets:
        return None

    # Drop if all prices are exactly 0.50 (untraded).
    if all(_market_price_dollars(m) == 0.50 for m in raw_markets):
        return None

    is_no_draw = sport in _NO_DRAW_SPORTS
    home, away = _extract_teams_from_title(raw.get("title", ""))

    # 2-way moneyline: exactly two contracts, complementary sides.
    # 3-way 1x2 (soccer): three contracts (home/draw/away).
    if is_no_draw and len(raw_markets) >= 2:
        # Pick the top two highest-volume markets as home/away.
        sorted_mkts = sorted(raw_markets, key=_market_volume_usd, reverse=True)[:2]
        outcomes = [
            {
                "name": "home" if i == 0 else "away",
                "odds": _price_to_odds(_market_price_dollars(m), fee_rate),
                "provider_meta": {
                    "ticker": m.get("ticker"),
                    "volume": _market_volume_usd(m),
                },
            }
            for i, m in enumerate(sorted_mkts)
        ]
        market = {"type": "moneyline", "outcomes": outcomes}
    elif not is_no_draw and len(raw_markets) >= 3:
        # Identify draw market by the literal "draw" keyword in yes_sub_title.
        def is_draw(m: dict) -> bool:
            return "draw" in str(m.get("yes_sub_title", "")).lower()

        draw_mkts = [m for m in raw_markets if is_draw(m)]
        non_draw = [m for m in raw_markets if not is_draw(m)]
        if len(draw_mkts) != 1 or len(non_draw) < 2:
            return None
        # Highest-volume non-draw is home; second is away.
        non_draw.sort(key=_market_volume_usd, reverse=True)
        ordered = [non_draw[0], draw_mkts[0], non_draw[1]]
        names = ["home", "draw", "away"]
        outcomes = [
            {
                "name": n,
                "odds": _price_to_odds(_market_price_dollars(m), fee_rate),
                "provider_meta": {
                    "ticker": m.get("ticker"),
                    "volume": _market_volume_usd(m),
                },
            }
            for n, m in zip(names, ordered)
        ]
        market = {"type": "1x2", "outcomes": outcomes}
    else:
        return None

    return StandardEvent(
        id=f"kalshi_{event_ticker}",
        name=raw.get("title", ""),
        sport=sport,
        markets=[market],
        provider="kalshi",
        url=f"https://kalshi.com/markets/{event_ticker}",
        home_team=home,
        away_team=away,
    )
