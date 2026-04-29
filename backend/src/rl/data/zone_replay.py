"""Forward-replay simulation for zone-touch outcome labelling.

For each historical zone touch (in stock_signals), step through subsequent
ticks in market_trades to determine whether REV or CONT would have won.
Used by `rl label-zone-outcomes` to generate retroactive (obs, action,
reward) tuples that bypass the live-execution gap.
"""

from __future__ import annotations

from dataclasses import dataclass

NQ_TICK_SIZE = 0.25


@dataclass
class ReplayOutcome:
    """Result of simulating a single (entry, side, stop, tp) over a tick stream."""

    pnl_r: float
    pnl_pts: float
    exit_reason: str  # "stop" | "tp" | "timeout"
    exit_price: float


def simulate_forward(
    ticks: list[tuple[float, float]],
    entry_price: float,
    side: str,
    stop_ticks: int,
    tp_ticks: int | None = None,
) -> ReplayOutcome:
    """Replay forward from entry through ticks; classify outcome.

    Args:
        ticks: list of (ts_seconds, price) AFTER the entry point.
        entry_price: assumed entry fill price.
        side: "long" or "short".
        stop_ticks: stop distance in NQ ticks (each tick = 0.25 pt).
        tp_ticks: optional TP distance; default = 2 * stop_ticks.

    Returns:
        ReplayOutcome with pnl_r normalized by stop distance (so a stopped
        trade returns -1.0R and a TP'd trade returns +tp_ticks/stop_ticks R).
    """
    if not ticks or stop_ticks <= 0:
        return ReplayOutcome(0.0, 0.0, "timeout", entry_price)

    if tp_ticks is None:
        tp_ticks = 2 * stop_ticks

    stop_offset = stop_ticks * NQ_TICK_SIZE
    tp_offset = tp_ticks * NQ_TICK_SIZE

    if side == "long":
        stop_price = entry_price - stop_offset
        tp_price = entry_price + tp_offset
    else:
        stop_price = entry_price + stop_offset
        tp_price = entry_price - tp_offset

    for _ts, price in ticks:
        if side == "long":
            if price <= stop_price:
                return ReplayOutcome(-1.0, -stop_offset, "stop", stop_price)
            if price >= tp_price:
                return ReplayOutcome(tp_ticks / stop_ticks, tp_offset, "tp", tp_price)
        else:
            if price >= stop_price:
                return ReplayOutcome(-1.0, -stop_offset, "stop", stop_price)
            if price <= tp_price:
                return ReplayOutcome(tp_ticks / stop_ticks, tp_offset, "tp", tp_price)

    # Window expired — mark to last price
    last_price = ticks[-1][1]
    if side == "long":
        pnl_pts = last_price - entry_price
    else:
        pnl_pts = entry_price - last_price
    pnl_r = pnl_pts / stop_offset
    return ReplayOutcome(pnl_r, pnl_pts, "timeout", last_price)
