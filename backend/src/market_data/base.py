"""Abstract market data provider interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class BarData:
    """Single OHLCV bar with delta."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    delta: int = 0  # buy_volume - sell_volume


@dataclass
class TickData:
    """Single trade tick with aggressor side."""
    timestamp: datetime
    price: float
    size: int
    side: str  # "buy" or "sell"


@dataclass
class MarketSnapshot:
    """Collection of bars and ticks for a session."""
    symbol: str
    date: str
    bars: list[BarData] = field(default_factory=list)
    ticks: list[TickData] = field(default_factory=list)
    last_price: float | None = None


class MarketDataProvider(ABC):
    """Abstract base for market data providers (Databento, IB, etc.)."""

    @abstractmethod
    async def get_bars(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> list[BarData]:
        """Fetch OHLCV bars. interval: '1m', '5m', '15m', '1h', '1d'."""
        ...

    @abstractmethod
    async def get_ticks(
        self, symbol: str, start: datetime, end: datetime
    ) -> list[TickData]:
        """Fetch tick-level trades with aggressor side."""
        ...

    @abstractmethod
    async def get_latest_price(self, symbol: str) -> float | None:
        """Get latest traded price."""
        ...
