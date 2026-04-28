"""Real-time position and P&L tracking from fills."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

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
        self.entry_order_id: int | None = None
        self.stop_order_id: int | None = None

        self.session_pnl: float = 0.0
        self.peak_equity: float = 0.0
        self.trade_count: int = 0
        self.consecutive_stops: int = 0
        self.last_trade_ts: float = 0.0
        self.fills: list[FillRecord] = []

        # Per-trade peak-R tracking — fed by update_mark() on each tick.
        # Reset at every open/close. Used by EarlyExit (lock at +0.5R) and
        # by the augmented-obs position_state fed back to live inference.
        self.peak_R: float = 0.0
        self.locked_half_R: bool = False
        # Once peak_R clears 2.0, the broker moves the stop to entry+small
        # buffer (covers fees + spread + tiny profit). Flag prevents re-firing
        # the modify_stop on every subsequent tick at peak_R>=2.0.
        self.locked_BE: bool = False

    @property
    def is_flat(self) -> bool:
        return self.side is None

    @property
    def trailing_dd(self) -> float:
        """Current drawdown from peak equity."""
        return max(0, self.peak_equity - self.session_pnl)

    def unrealized_R(self, current_price: float) -> float:
        """R-multiple of current mark vs entry, using stop distance as the unit."""
        if self.is_flat or self.entry_price <= 0 or self.stop_price <= 0:
            return 0.0
        risk_unit = abs(self.entry_price - self.stop_price)
        if risk_unit <= 0:
            return 0.0
        move = (current_price - self.entry_price) if self.side == "long" else (self.entry_price - current_price)
        return move / risk_unit

    def update_mark(self, current_price: float) -> float:
        """Update peak_R from current price. Returns the fresh unrealized_R."""
        r = self.unrealized_R(current_price)
        if r > self.peak_R:
            self.peak_R = r
        return r

    def on_fill(self, side: str, price: float, size: int, stop_price: float, signal_price: float = 0.0) -> None:
        """Record a new position entry."""
        self.side = side
        self.entry_price = price
        self.stop_price = stop_price
        self.size = size
        self.last_trade_ts = time.time()
        self.fills.append(
            FillRecord(
                ts=self.last_trade_ts,
                side=side,
                price=price,
                size=size,
                signal_price=signal_price,
            )
        )
        log.info("Position opened: %s %d @ %.2f stop=%.2f", side, size, price, stop_price)

    def on_add(self, price: float, add_size: int) -> None:
        """Record a pyramid add. Volume-weighted average entry price.

        Pyramid adds change the entry basis (averaged toward `price`) which silently
        invalidates `peak_R` — it was tracked in R units of the pre-add basis.
        Recompute it in the new basis using the implied peak price so the
        EarlyExit lock check stays meaningful after the add.
        """
        if self.is_flat or add_size <= 0:
            return

        # Convert peak_R from old basis to peak_price, then back to new basis.
        old_entry = self.entry_price
        old_risk_unit = abs(old_entry - self.stop_price) if self.stop_price > 0 else 0.0
        peak_price: float | None = None
        if old_risk_unit > 0 and self.peak_R > 0:
            if self.side == "long":
                peak_price = old_entry + self.peak_R * old_risk_unit
            else:
                peak_price = old_entry - self.peak_R * old_risk_unit

        total = self.size + add_size
        self.entry_price = (old_entry * self.size + price * add_size) / total
        self.size = total
        self.last_trade_ts = time.time()
        self.fills.append(FillRecord(ts=self.last_trade_ts, side=self.side or "", price=price, size=add_size))

        if peak_price is not None:
            new_risk_unit = abs(self.entry_price - self.stop_price)
            if new_risk_unit > 0:
                if self.side == "long":
                    self.peak_R = max(0.0, (peak_price - self.entry_price) / new_risk_unit)
                else:
                    self.peak_R = max(0.0, (self.entry_price - peak_price) / new_risk_unit)

        log.info(
            "Pyramid add: +%d @ %.2f -> size=%d avg_entry=%.4f peak_R=%.3f",
            add_size,
            price,
            self.size,
            self.entry_price,
            self.peak_R,
        )

    def on_exit(self, exit_price: float, was_stop: bool = False) -> float:
        """Record position exit. Returns P&L in dollars."""
        if self.side is None:
            return 0.0

        pnl_pts = exit_price - self.entry_price if self.side == "long" else self.entry_price - exit_price

        pnl_dollars = pnl_pts * self._point_value * self.size
        self.session_pnl += pnl_dollars
        self.peak_equity = max(self.peak_equity, self.session_pnl)
        self.trade_count += 1

        if was_stop:
            self.consecutive_stops += 1
        else:
            self.consecutive_stops = 0

        log.info(
            "Position closed: %s @ %.2f pnl=$%.2f (session=$%.2f)", self.side, exit_price, pnl_dollars, self.session_pnl
        )

        self.side = None
        self.entry_price = 0.0
        self.stop_price = 0.0
        self.size = 0
        self.entry_order_id = None
        self.stop_order_id = None
        self.peak_R = 0.0
        self.locked_half_R = False
        self.locked_BE = False

        return pnl_dollars

    def exceeds_daily_loss(self, max_loss: float) -> bool:
        return self.session_pnl <= -abs(max_loss)

    def exceeds_trailing_dd(self, max_dd: float) -> bool:
        return self.trailing_dd >= abs(max_dd)
