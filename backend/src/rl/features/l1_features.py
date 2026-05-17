"""L1-aware orderflow feature primitives.

These functions consume an `L1Snapshot` (top-of-book at a given moment)
plus a list of recent trade dicts, and produce the OF features that
require book context to compute correctly. Replaces the candle-derived
approximations in `orderflow_features.py` for the dims that can be
properly computed from L1 + trades.

NOTE: still uses approximations where L2 would be needed:
  - stacked_imbalance_count (depth>1): can't measure from L1
  - imbalance_density across multiple levels: can't measure from L1
  Those are left to candle-derived computation as a fallback.

This module is pure — no I/O, no side effects, no class state. Inputs
in, dict out. Easy to test, easy to backtest.
"""

from __future__ import annotations

from typing import Literal

from ...market_data.l1_quote_state import L1Snapshot

TICK_SIZE = 0.25
_PRICE_EPS = TICK_SIZE / 100.0  # float guard: < 0.5 tick, never reclassifies level


def compute_true_spread_ticks(snapshot: L1Snapshot) -> float:
    return snapshot.spread_ticks


def compute_top_imbalance(snapshot: L1Snapshot) -> float:
    return snapshot.top_imbalance


def classify_trade_lee_ready(
    trade_price: float,
    snapshot: L1Snapshot,
    prev_trade_price: float | None = None,
) -> Literal["buy", "sell", "unknown"]:
    """Lee-Ready trade classification.

    - trade_price >= ask → buy aggressor (lifted offer)
    - trade_price <= bid → sell aggressor (hit bid)
    - inside spread → tick-rule (vs previous trade price)
    """
    if trade_price >= snapshot.ask - _PRICE_EPS:
        return "buy"
    if trade_price <= snapshot.bid + _PRICE_EPS:
        return "sell"
    if prev_trade_price is None:
        return "unknown"
    if trade_price > prev_trade_price:
        return "buy"
    if trade_price < prev_trade_price:
        return "sell"
    return "unknown"


def aggressor_side(
    trades: list[dict],
    snapshot: L1Snapshot,
) -> tuple[int, int]:
    """Split trade volume into (passive_volume, active_volume).

    'Active' = trade volume where price >= ask or <= bid (clear aggressor).
    'Passive' = trade volume strictly inside spread (midpoint — uncertain).
    """
    passive = 0
    active = 0
    for t in trades:
        price = float(t.get("price", 0))
        size = int(t.get("size", 0))
        if price <= 0 or size <= 0:
            continue
        if price >= snapshot.ask - _PRICE_EPS or price <= snapshot.bid + _PRICE_EPS:
            active += size
        else:
            passive += size
    return passive, active


def detect_absorption_l1(
    trades: list[dict],
    snap_before: L1Snapshot | None,
    snap_after: L1Snapshot | None,
) -> float:
    """Score [0,1]: how much trade volume hit a level without
    proportional book displacement (= passive size refreshed/absorbed).

    Heuristic:
        total_hit = sum of trade sizes at ask (buy aggression)
        actual_displacement = ask_size_before - ask_size_after (if ask price unchanged)
        absorption_ratio = 1 - displacement / hit  (clamped to [0,1])

    High score = lots traded, book barely moved → strong passive absorption
    (iceberg or hidden orders refreshing).
    """
    if not trades or snap_before is None or snap_after is None:
        return 0.0

    # Only score when the inside price didn't shift (otherwise displacement
    # is the obvious explanation)
    ask_price_stable = abs(snap_before.ask - snap_after.ask) < TICK_SIZE / 2
    bid_price_stable = abs(snap_before.bid - snap_after.bid) < TICK_SIZE / 2

    if not (ask_price_stable or bid_price_stable):
        return 0.0

    # Aggregate buy-side aggression at the ask
    buy_hit = sum(int(t.get("size", 0)) for t in trades if float(t.get("price", 0)) >= snap_before.ask - _PRICE_EPS)
    sell_hit = sum(int(t.get("size", 0)) for t in trades if float(t.get("price", 0)) <= snap_before.bid + _PRICE_EPS)

    # Score the side with more aggression
    if buy_hit >= sell_hit and ask_price_stable:
        displacement = max(0, snap_before.ask_size - snap_after.ask_size)
        if buy_hit <= 0:
            return 0.0
        absorption = 1.0 - (displacement / buy_hit)
        return max(0.0, min(1.0, absorption))

    if sell_hit > 0 and bid_price_stable:
        displacement = max(0, snap_before.bid_size - snap_after.bid_size)
        absorption = 1.0 - (displacement / sell_hit)
        return max(0.0, min(1.0, absorption))

    return 0.0


def compute_l1_features(
    snapshot: L1Snapshot | None,
    recent_trades: list[dict],
) -> dict[str, float]:
    """One-shot computation of L1 features from a current snapshot + recent trades.

    Returns dict with keys: spread_ticks, top_imbalance, passive_active_ratio,
    active_buy_volume, active_sell_volume, trade_count.

    Gracefully returns zeros when snapshot is None (L1 unavailable).
    """
    if snapshot is None:
        return {
            "spread_ticks": 0.0,
            "top_imbalance": 0.0,
            "passive_active_ratio": 0.0,
            "active_buy_volume": 0.0,
            "active_sell_volume": 0.0,
            "trade_count": 0.0,
        }

    spread = compute_true_spread_ticks(snapshot)
    imb = compute_top_imbalance(snapshot)
    passive_vol, active_vol = aggressor_side(recent_trades, snapshot)

    # passive/active ratio: high = passive flow dominant (weaker hand winning)
    pa_ratio = min(passive_vol / max(active_vol, 1), 5.0)

    # Split active by direction
    active_buy = sum(
        int(t.get("size", 0)) for t in recent_trades if float(t.get("price", 0)) >= snapshot.ask - _PRICE_EPS
    )
    active_sell = sum(
        int(t.get("size", 0)) for t in recent_trades if float(t.get("price", 0)) <= snapshot.bid + _PRICE_EPS
    )

    return {
        "spread_ticks": float(spread),
        "top_imbalance": float(imb),
        "passive_active_ratio": float(pa_ratio),
        "active_buy_volume": float(active_buy),
        "active_sell_volume": float(active_sell),
        "trade_count": float(len(recent_trades)),
    }
