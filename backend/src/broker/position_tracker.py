"""Real-time position and P&L tracking from fills."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# NQ point value: $20 per point (4 ticks per point, $5 per tick)
NQ_POINT_VALUE = 20.0


@dataclass
class FillRecord:
    """Record of a single fill."""
    ts: float
    side: str
    price: float
    size: int
    signal_price: float = 0.0  # price when signal fired (for slippage calc)


class PositionTracker:
    """Tracks position state, session P&L, and risk metrics."""

    def __init__(self, point_value: float = NQ_POINT_VALUE) -> None:
        self._point_value = point_value
        self.reset_session()

    def reset_session(self) -> None:
        """Reset all session state (call at start of day)."""
        self.side: str | None = None
        self.entry_price: float = 0.0
        self.stop_price: float = 0.0
        self.size: int = 0
        self.stop_order_id: int | None = None

        self.session_pnl: float = 0.0
        self.peak_equity: float = 0.0
        self.trade_count: int = 0
        self.consecutive_stops: int = 0
        self.last_trade_ts: float = 0.0
        self.fills: list[FillRecord] = []

    @property
    def is_flat(self) -> bool:
        return self.side is None

    @property
    def trailing_dd(self) -> float:
        """Current drawdown from peak equity."""
        return max(0, self.peak_equity - self.session_pnl)

    def on_fill(self, side: str, price: float, size: int, stop_price: float,
                signal_price: float = 0.0) -> None:
        """Record a new position entry."""
        self.side = side
        self.entry_price = price
        self.stop_price = stop_price
        self.size = size
        self.last_trade_ts = time.time()
        self.fills.append(FillRecord(
            ts=self.last_trade_ts, side=side, price=price, size=size,
            signal_price=signal_price,
        ))
        log.info("Position opened: %s %d @ %.2f stop=%.2f", side, size, price, stop_price)

    def on_exit(self, exit_price: float, was_stop: bool = False) -> float:
        """Record position exit. Returns P&L in dollars."""
        if self.side is None:
            return 0.0

        if self.side == "long":
            pnl_pts = exit_price - self.entry_price
        else:
            pnl_pts = self.entry_price - exit_price

        pnl_dollars = pnl_pts * self._point_value * self.size
        self.session_pnl += pnl_dollars
        self.peak_equity = max(self.peak_equity, self.session_pnl)
        self.trade_count += 1

        if was_stop:
            self.consecutive_stops += 1
        else:
            self.consecutive_stops = 0

        log.info("Position closed: %s @ %.2f pnl=$%.2f (session=$%.2f)",
                 self.side, exit_price, pnl_dollars, self.session_pnl)

        self.side = None
        self.entry_price = 0.0
        self.stop_price = 0.0
        self.size = 0
        self.stop_order_id = None

        return pnl_dollars

    def exceeds_daily_loss(self, max_loss: float) -> bool:
        return self.session_pnl <= -abs(max_loss)

    def exceeds_trailing_dd(self, max_dd: float) -> bool:
        return self.trailing_dd >= abs(max_dd)
