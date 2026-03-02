"""
PolymarketDataClient — REST client for Polymarket's public Data API.

Fetches portfolio positions, closed positions, and trade history using
only a wallet address. No authentication required — this data is public
(it's on the Polygon blockchain).

Endpoints:
    - GET /positions?user=0x{addr} → open positions
    - GET /closed-positions?user=0x{addr} → resolved positions
    - GET /trades?user=0x{addr} → trade history
"""

import logging
from dataclasses import dataclass, field

import aiohttp

logger = logging.getLogger(__name__)

DATA_API_BASE = "https://data-api.polymarket.com"


# ───────────────────────── Data models ─────────────────────────


@dataclass
class PolyPosition:
    """An open position on Polymarket."""
    condition_id: str
    title: str
    outcome: str
    size: float              # Number of shares held
    avg_price: float         # Average entry price (0-1)
    current_value: float     # Current mark-to-market value (USDC)
    cur_price: float         # Current market price (0-1)
    cash_pnl: float          # Unrealized P&L in USDC
    percent_pnl: float       # Unrealized P&L %
    realized_pnl: float      # Realized P&L from partial closes
    redeemable: bool         # Can be redeemed (market resolved)
    initial_value: float = 0.0
    slug: str = ""
    event_slug: str = ""


@dataclass
class PolyClosedPosition:
    """A resolved/closed position on Polymarket."""
    condition_id: str
    title: str
    outcome: str
    avg_price: float         # Average entry price
    total_bought: float      # Total shares purchased
    realized_pnl: float      # Realized P&L in USDC
    end_date: str = ""       # Market resolution date
    cur_price: float = 0.0   # Final price (1.0 if won, 0.0 if lost)


@dataclass
class PolyTrade:
    """A single trade on Polymarket."""
    condition_id: str
    title: str
    outcome: str
    side: str                # "BUY" or "SELL"
    size: float              # Shares traded
    price: float             # Trade price (0-1)
    timestamp: str           # ISO timestamp
    usdc_size: float = 0.0   # USD value
    transaction_hash: str = ""


@dataclass
class PolyPortfolio:
    """Aggregated portfolio snapshot."""
    positions: list[PolyPosition] = field(default_factory=list)
    closed_positions: list[PolyClosedPosition] = field(default_factory=list)
    total_value_usdc: float = 0.0
    total_pnl_usdc: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    open_count: int = 0
    closed_count: int = 0


# ───────────────────────── Client ─────────────────────────


class PolymarketDataClient:
    """REST client for Polymarket's public Data API."""

    def __init__(self, base_url: str = DATA_API_BASE, timeout: int = 15):
        self.base_url = base_url
        self.timeout = aiohttp.ClientTimeout(total=timeout)

    async def _get(self, path: str, params: dict | None = None) -> list | dict:
        """Make a GET request to the Data API."""
        url = f"{self.base_url}{path}"
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"Data API {resp.status}: {text[:200]}")
                return await resp.json()

    async def get_positions(self, wallet: str) -> list[PolyPosition]:
        """Fetch open positions for a wallet address."""
        data = await self._get("/positions", {"user": wallet, "limit": "500", "sizeThreshold": "0.01"})
        if not isinstance(data, list):
            return []

        positions = []
        for row in data:
            try:
                positions.append(PolyPosition(
                    condition_id=row.get("conditionId", ""),
                    title=row.get("title", ""),
                    outcome=row.get("outcome", ""),
                    size=float(row.get("size", 0)),
                    avg_price=float(row.get("avgPrice", 0)),
                    current_value=float(row.get("currentValue", 0)),
                    cur_price=float(row.get("curPrice", 0)),
                    cash_pnl=float(row.get("cashPnl", 0)),
                    percent_pnl=float(row.get("percentPnl", 0)),
                    realized_pnl=float(row.get("realizedPnl", 0)),
                    redeemable=bool(row.get("redeemable", False)),
                    initial_value=float(row.get("initialValue", 0)),
                    slug=row.get("slug", ""),
                    event_slug=row.get("eventSlug", ""),
                ))
            except (ValueError, TypeError) as e:
                logger.debug(f"Skipping position: {e}")
        return positions

    async def get_closed_positions(self, wallet: str, limit: int = 100) -> list[PolyClosedPosition]:
        """Fetch closed/resolved positions for a wallet address."""
        data = await self._get("/closed-positions", {"user": wallet, "limit": str(limit)})
        if not isinstance(data, list):
            return []

        closed = []
        for row in data:
            try:
                closed.append(PolyClosedPosition(
                    condition_id=row.get("conditionId", ""),
                    title=row.get("title", ""),
                    outcome=row.get("outcome", ""),
                    avg_price=float(row.get("avgPrice", 0)),
                    total_bought=float(row.get("totalBought", 0)),
                    realized_pnl=float(row.get("realizedPnl", 0)),
                    end_date=row.get("endDate", ""),
                    cur_price=float(row.get("curPrice", 0)),
                ))
            except (ValueError, TypeError) as e:
                logger.debug(f"Skipping closed position: {e}")
        return closed

    async def get_trades(self, wallet: str, limit: int = 100) -> list[PolyTrade]:
        """Fetch recent trade history for a wallet address."""
        data = await self._get("/trades", {"user": wallet, "limit": str(limit)})
        if not isinstance(data, list):
            return []

        trades = []
        for row in data:
            try:
                trades.append(PolyTrade(
                    condition_id=row.get("conditionId", ""),
                    title=row.get("title", ""),
                    outcome=row.get("outcome", ""),
                    side=row.get("side", ""),
                    size=float(row.get("size", 0)),
                    price=float(row.get("price", 0)),
                    timestamp=row.get("timestamp", ""),
                    usdc_size=float(row.get("usdcSize", 0)),
                    transaction_hash=row.get("transactionHash", ""),
                ))
            except (ValueError, TypeError) as e:
                logger.debug(f"Skipping trade: {e}")
        return trades

    async def get_portfolio(self, wallet: str) -> PolyPortfolio:
        """Fetch aggregated portfolio: positions + closed positions."""
        positions = await self.get_positions(wallet)
        closed = await self.get_closed_positions(wallet)

        total_value = sum(p.current_value for p in positions)
        unrealized = sum(p.cash_pnl for p in positions)
        realized = sum(c.realized_pnl for c in closed)
        total_pnl = unrealized + realized

        return PolyPortfolio(
            positions=positions,
            closed_positions=closed,
            total_value_usdc=round(total_value, 2),
            total_pnl_usdc=round(total_pnl, 2),
            unrealized_pnl=round(unrealized, 2),
            realized_pnl=round(realized, 2),
            open_count=len(positions),
            closed_count=len(closed),
        )
