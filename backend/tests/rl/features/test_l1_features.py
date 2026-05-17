from src.market_data.l1_quote_state import L1Snapshot
from src.rl.features.l1_features import (
    aggressor_side,
    classify_trade_lee_ready,
    compute_l1_features,
    compute_top_imbalance,
    compute_true_spread_ticks,
    detect_absorption_l1,
)


def test_true_spread_in_ticks():
    snap = L1Snapshot(bid=25000.0, ask=25000.25, bid_size=10, ask_size=10, ts=1.0)
    assert compute_true_spread_ticks(snap) == 1.0


def test_top_imbalance_bid_heavy():
    snap = L1Snapshot(bid=25000.0, ask=25000.25, bid_size=30, ask_size=10, ts=1.0)
    assert compute_top_imbalance(snap) == 0.5


def test_classify_trade_lee_ready_buy_at_ask():
    """Trade price == ask → buy aggressor."""
    snap = L1Snapshot(bid=25000.0, ask=25000.25, bid_size=10, ask_size=10, ts=1.0)
    assert classify_trade_lee_ready(trade_price=25000.25, snapshot=snap) == "buy"


def test_classify_trade_lee_ready_sell_at_bid():
    snap = L1Snapshot(bid=25000.0, ask=25000.25, bid_size=10, ask_size=10, ts=1.0)
    assert classify_trade_lee_ready(trade_price=25000.0, snapshot=snap) == "sell"


def test_classify_trade_lee_ready_midpoint_inferred_by_tick_rule():
    """Trade strictly inside spread — use tick-rule fallback."""
    snap = L1Snapshot(bid=25000.0, ask=25000.50, bid_size=10, ask_size=10, ts=1.0)
    # Tick-rule: trade above previous = buy, below = sell, equal = previous
    assert classify_trade_lee_ready(trade_price=25000.25, snapshot=snap, prev_trade_price=25000.0) == "buy"
    assert classify_trade_lee_ready(trade_price=25000.25, snapshot=snap, prev_trade_price=25000.50) == "sell"


def test_aggressor_side_passive_active_decomposition():
    """Given a list of trade dicts with prices + sizes, classify each
    via L1 snapshot and return (passive_volume, active_volume)."""
    snap = L1Snapshot(bid=25000.0, ask=25000.25, bid_size=10, ask_size=10, ts=1.0)
    trades = [
        {"price": 25000.25, "size": 5},  # buy aggressor (active buy)
        {"price": 25000.0, "size": 8},  # sell aggressor (active sell)
        {"price": 25000.25, "size": 3},  # buy aggressor (active buy)
    ]
    # 'active' = volume where price hit best bid or lifted best ask
    # in this test all trades match snap exactly → all active
    passive_vol, active_vol = aggressor_side(trades, snap)
    assert passive_vol == 0
    assert active_vol == 16


def test_detect_absorption_l1_heavy_volume_no_book_displacement():
    """Lots of trade volume hits the ask but bestAskSize barely moves
    → passive offers are absorbing the buying pressure."""
    snap_before = L1Snapshot(bid=25000.0, ask=25000.25, bid_size=10, ask_size=50, ts=1.0)
    snap_after = L1Snapshot(bid=25000.0, ask=25000.25, bid_size=10, ask_size=48, ts=2.0)
    trades = [{"price": 25000.25, "size": 20, "ts": 1.5}]  # 20 contracts hit
    score = detect_absorption_l1(trades=trades, snap_before=snap_before, snap_after=snap_after)
    # 20 contracts traded but ask size only dropped by 2 (refresh detected)
    # → strong absorption (>0.5)
    assert score > 0.5


def test_detect_absorption_l1_no_absorption_when_book_clears():
    """20 contracts hit the ask and ask size dropped by 20 → no refresh, no absorption."""
    snap_before = L1Snapshot(bid=25000.0, ask=25000.25, bid_size=10, ask_size=50, ts=1.0)
    snap_after = L1Snapshot(bid=25000.0, ask=25000.50, bid_size=10, ask_size=30, ts=2.0)
    trades = [{"price": 25000.25, "size": 20, "ts": 1.5}]
    score = detect_absorption_l1(trades=trades, snap_before=snap_before, snap_after=snap_after)
    assert score < 0.3


def test_compute_l1_features_returns_dict_with_expected_keys():
    snap = L1Snapshot(bid=25000.0, ask=25000.25, bid_size=10, ask_size=10, ts=1.0)
    trades = [{"price": 25000.25, "size": 5}, {"price": 25000.0, "size": 3}]
    feats = compute_l1_features(snapshot=snap, recent_trades=trades)
    expected_keys = {
        "spread_ticks",
        "top_imbalance",
        "passive_active_ratio",
        "active_buy_volume",
        "active_sell_volume",
        "trade_count",
    }
    assert set(feats.keys()) == expected_keys


def test_compute_l1_features_handles_none_snapshot():
    """When L1 state is unavailable, return zeros (graceful degradation)."""
    feats = compute_l1_features(snapshot=None, recent_trades=[])
    assert feats["spread_ticks"] == 0.0
    assert feats["top_imbalance"] == 0.0
    assert feats["passive_active_ratio"] == 0.0
