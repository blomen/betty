"""
PolymarketDataClient — REST client for Polymarket's public Data API.

Fetches portfolio positions, closed positions, and trade history using
only a wallet address. No authentication required — this data is public
(it's on the Polygon blockchain).

Endpoints:
    - GET /positions?user=0x{addr} → open positions
    - GET /closed-positions?user=0x{addr} → resolved positions
    - GET /trades?user=0x{addr} → trade history
    - Polygon RPC eth_call → on-chain USDC.e balance
"""

import logging
from dataclasses import dataclass, field

import aiohttp

logger = logging.getLogger(__name__)

DATA_API_BASE = "https://data-api.polymarket.com"

# USDC.e on Polygon (6 decimals) — used by Polymarket
USDC_E_CONTRACT = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
# Public Polygon RPC endpoints (fallback chain, all free/no-key-required)
POLYGON_RPCS = [
    "https://polygon-bor-rpc.publicnode.com",
    "https://1rpc.io/matic",
    "https://polygon.drpc.org",
]


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
    cash_balance: float = 0.0        # USDC.e sitting in wallet (not in positions)
    position_value: float = 0.0      # Active (non-resolved) position market value
    redeemable_value: float = 0.0    # Winning resolved positions (can redeem for $1/share)


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

    async def get_usdc_balance(self, wallet: str) -> float:
        """Query on-chain USDC.e balance on Polygon via public RPC.

        Returns the USDC balance (float, 2 decimal places).
        This is the cash sitting in the Polymarket proxy wallet — not in positions.
        """
        wallet_clean = wallet.lower().replace("0x", "")
        # ERC-20 balanceOf(address) — function selector 0x70a08231
        call_data = f"0x70a08231000000000000000000000000{wallet_clean}"
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_call",
            "params": [{"to": USDC_E_CONTRACT, "data": call_data}, "latest"],
            "id": 1,
        }

        for rpc_url in POLYGON_RPCS:
            try:
                async with aiohttp.ClientSession(timeout=self.timeout) as session:
                    async with session.post(rpc_url, json=payload) as resp:
                        if resp.status != 200:
                            continue
                        result = await resp.json()
                        # RPC errors come back as {"error": {...}} with 200 status
                        if "error" in result:
                            logger.debug(f"Polygon RPC {rpc_url} returned error: {result['error']}")
                            continue
                        hex_balance = result.get("result", "0x0")
                        if not hex_balance or hex_balance == "0x":
                            hex_balance = "0x0"
                        raw = int(hex_balance, 16)
                        return round(raw / 1e6, 2)  # USDC has 6 decimals
            except Exception as e:
                logger.debug(f"Polygon RPC {rpc_url} failed: {e}")
                continue

        logger.warning("[PolyClient] All Polygon RPCs failed for USDC balance")
        return 0.0

    async def get_portfolio(self, wallet: str) -> PolyPortfolio:
        """Fetch aggregated portfolio: positions + closed positions + on-chain USDC balance.

        Total value = cash_balance + active_position_value + redeemable_value
        Where:
          - cash_balance: USDC.e in the wallet (not deployed in positions)
          - active_position_value: market value of non-resolved positions
          - redeemable_value: winning resolved positions (shares * $1, can be redeemed)
        """
        positions = await self.get_positions(wallet)
        closed = await self.get_closed_positions(wallet)
        cash_balance = await self.get_usdc_balance(wallet)

        # Separate active (trading) vs resolved (redeemable) positions
        active = [p for p in positions if not p.redeemable]
        redeemable = [p for p in positions if p.redeemable]

        # Active position value = sum of current market values
        position_value = sum(p.current_value for p in active)

        # Redeemable value = shares from winning resolved positions
        # If curPrice > 0 the position won (each share redeems at $1)
        # If curPrice == 0 the position lost (redeemable but worth $0)
        redeemable_value = sum(p.size for p in redeemable if p.cur_price > 0)

        total_value = cash_balance + position_value + redeemable_value
        unrealized = sum(p.cash_pnl for p in active)
        realized = sum(c.realized_pnl for c in closed)
        total_pnl = unrealized + realized

        return PolyPortfolio(
            positions=positions,
            closed_positions=closed,
            total_value_usdc=round(total_value, 2),
            total_pnl_usdc=round(total_pnl, 2),
            unrealized_pnl=round(unrealized, 2),
            realized_pnl=round(realized, 2),
            open_count=len(active),
            closed_count=len(closed),
            cash_balance=round(cash_balance, 2),
            position_value=round(position_value, 2),
            redeemable_value=round(redeemable_value, 2),
        )
