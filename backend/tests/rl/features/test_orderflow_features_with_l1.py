"""Integration test: same candle data but with vs without L1 snapshot
produces different (L1-improved) values for the L1-derived dims."""

from datetime import datetime, timezone

import numpy as np
import pytest

from src.market_data.l1_quote_state import L1Snapshot
from src.market_data.orderflow import CandleFlow
from src.rl.features.orderflow_features import extract_orderflow_features


def _make_candle(volume=100, delta=50, body_ratio=0.5):
    return CandleFlow(
        ts=datetime(2026, 5, 17, 14, 30, tzinfo=timezone.utc),
        open=25000.0,
        high=25000.5,
        low=24999.5,
        close=25000.25,
        volume=volume,
        buy_volume=int(volume * 0.6),
        sell_volume=int(volume * 0.4),
        delta=delta,
        tick_count=10,
        spread=1.0,
    )


def test_extract_with_l1_overrides_spread_ticks():
    candles = [_make_candle() for _ in range(5)]
    l1_snap = L1Snapshot(bid=25000.0, ask=25000.50, bid_size=10, ask_size=10, ts=1.0)
    feats_no_l1 = extract_orderflow_features(candles, signals=None, l1_snapshot=None, recent_trades=None)
    feats_with_l1 = extract_orderflow_features(candles, signals=None, l1_snapshot=l1_snap, recent_trades=[])

    # spread_ticks is index 6 in the 21-dim vector
    SPREAD_IDX = 6
    # L1 spread = (25000.50 - 25000.00) / 0.25 = 2 ticks, normalized /50 = 0.04
    assert feats_with_l1[SPREAD_IDX] == pytest.approx(2.0 / 50.0, abs=1e-4)
    # Without L1, spread comes from candle (high-low = 1.0 → 4 ticks → 0.08)
    assert feats_no_l1[SPREAD_IDX] == pytest.approx(4.0 / 50.0, abs=1e-4)


def test_extract_with_l1_overrides_passive_active_ratio():
    candles = [_make_candle() for _ in range(5)]
    l1_snap = L1Snapshot(bid=25000.0, ask=25000.25, bid_size=10, ask_size=10, ts=1.0)
    trades = [
        {"price": 25000.25, "size": 10},  # buy aggressor (active)
        {"price": 25000.25, "size": 5},  # buy aggressor (active)
        {"price": 25000.10, "size": 20},  # inside spread → passive
    ]
    feats = extract_orderflow_features(candles, signals=None, l1_snapshot=l1_snap, recent_trades=trades)
    # passive_active_ratio is index 7
    PA_IDX = 7
    # passive=20, active=15 → ratio = 20/15 = 1.33, normalized /5 = 0.267
    assert feats[PA_IDX] == pytest.approx(1.333 / 5.0, abs=0.01)


def test_extract_without_l1_preserves_candle_behavior():
    """Calling without l1_snapshot should produce identical output to
    the legacy call signature (backward compatibility)."""
    candles = [_make_candle() for _ in range(5)]
    feats_new = extract_orderflow_features(candles, signals=None, l1_snapshot=None, recent_trades=None)
    feats_legacy = extract_orderflow_features(candles, signals=None)
    np.testing.assert_array_equal(feats_new, feats_legacy)
