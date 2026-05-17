"""Top-of-book L1 quote state — bestBid/bestAsk and their sizes from
TopstepX GatewayQuote events. Maintained on LevelMonitor so feature
extractors can read the latest book state synchronously without an
asyncio await.

L2 depth is intentionally NOT modeled here; that's a separate subscription
and the v6 plan covers it. This is the no-money path — L1 is free and
the dominant OF features (spread, passive/active classification, top-of-
book imbalance, absorption) can be computed from it alone.
"""

from __future__ import annotations

from dataclasses import dataclass

TICK_SIZE = 0.25


@dataclass(frozen=True)
class L1Snapshot:
    """Immutable point-in-time snapshot of top-of-book."""

    bid: float
    ask: float
    bid_size: int
    ask_size: int
    ts: float

    @property
    def spread_ticks(self) -> float:
        if self.ask <= 0 or self.bid <= 0:
            return 0.0
        return (self.ask - self.bid) / TICK_SIZE

    @property
    def top_imbalance(self) -> float:
        """(bid_size - ask_size) / total. Range [-1, +1]. Positive = bid-heavy."""
        total = self.bid_size + self.ask_size
        if total <= 0:
            return 0.0
        return (self.bid_size - self.ask_size) / total


class L1QuoteState:
    """Mutable holder for the latest L1 snapshot. Thread-safe for the
    expected single-writer (stream handler) + multi-reader (feature
    extractors on the asyncio loop) pattern.
    """

    def __init__(self) -> None:
        self._snapshot: L1Snapshot | None = None

    def update(
        self,
        bid: float,
        ask: float,
        bid_size: int,
        ask_size: int,
        ts: float,
    ) -> None:
        # Reject crossed/invalid books — keep last valid snapshot
        if bid <= 0 or ask <= 0 or bid >= ask:
            return
        clean_bid_size = max(0, int(bid_size))
        clean_ask_size = max(0, int(ask_size))
        self._snapshot = L1Snapshot(
            bid=bid,
            ask=ask,
            bid_size=clean_bid_size,
            ask_size=clean_ask_size,
            ts=ts,
        )

    def snapshot(self) -> L1Snapshot | None:
        return self._snapshot
