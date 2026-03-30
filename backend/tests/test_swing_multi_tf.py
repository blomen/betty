from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from src.market_data.levels import (
    aggregate_to_timeframe,
    detect_fractal_pivots,
    compute_multi_tf_swings,
    SwingLevel,
    TimeframeSwings,
    SwingStructure,
)

CET = ZoneInfo("Europe/Stockholm")


def _make_1m_bars(days: int = 5, base_price: float = 19000.0) -> list[dict]:
    """Generate synthetic 1m bars across multiple trading days (00:00-22:00 CET)."""
    bars = []
    start = datetime(2026, 3, 23, 0, 0, tzinfo=CET)  # Monday
    for d in range(days):
        day_start = start + timedelta(days=d)
        if day_start.weekday() >= 5:  # skip weekends
            continue
        for minute in range(0, 22 * 60, 1):  # 00:00 to 22:00
            ts = day_start + timedelta(minutes=minute)
            import math
            progress = d * 22 * 60 + minute
            noise = math.sin(progress / 60.0) * 20
            price = base_price + d * 50 + noise
            bars.append({
                "ts": ts.astimezone(timezone.utc),
                "high": price + 5,
                "low": price - 5,
                "open": price - 2,
                "close": price + 2,
            })
    return bars


def test_aggregate_daily():
    bars = _make_1m_bars(days=5)
    daily = aggregate_to_timeframe(bars, "daily")
    assert len(daily) >= 3
    assert all(d["high"] >= d["low"] for d in daily)
    assert all(d["open"] > 0 for d in daily)
    assert all("date" in d and "ts" in d for d in daily)
    assert daily[0]["ts"] <= daily[-1]["ts"]


def test_aggregate_weekly():
    bars = _make_1m_bars(days=12)
    weekly = aggregate_to_timeframe(bars, "weekly")
    assert len(weekly) >= 1
    assert weekly[0]["high"] >= weekly[0]["low"]


def test_aggregate_monthly():
    bars = _make_1m_bars(days=30)
    monthly = aggregate_to_timeframe(bars, "monthly")
    assert len(monthly) >= 1


def test_aggregate_empty():
    result = aggregate_to_timeframe([], "daily")
    assert result == []


# ---------------------------------------------------------------------------
# Task 2: detect_fractal_pivots
# ---------------------------------------------------------------------------

def _make_uptrend_candles() -> list[dict]:
    """Candles with clear HH/HL pattern. Lookback=3 needs 7 bars per pivot."""
    return [
        {"high": 100, "low": 95, "close": 98, "ts": 1000},
        {"high": 103, "low": 98, "close": 101, "ts": 2000},
        {"high": 106, "low": 101, "close": 104, "ts": 3000},
        {"high": 109, "low": 104, "close": 107, "ts": 4000},
        {"high": 110, "low": 105, "close": 108, "ts": 5000},  # SH1
        {"high": 107, "low": 100, "close": 103, "ts": 6000},
        {"high": 104, "low": 97, "close": 100, "ts": 7000},
        {"high": 101, "low": 94, "close": 97, "ts": 8000},
        {"high": 98, "low": 93, "close": 95, "ts": 9000},   # SL1
        {"high": 102, "low": 96, "close": 100, "ts": 10000},
        {"high": 108, "low": 102, "close": 106, "ts": 11000},
        {"high": 114, "low": 108, "close": 112, "ts": 12000},
        {"high": 119, "low": 113, "close": 117, "ts": 13000},
        {"high": 122, "low": 116, "close": 120, "ts": 14000}, # SH2 (HH)
        {"high": 118, "low": 112, "close": 115, "ts": 15000},
        {"high": 114, "low": 108, "close": 111, "ts": 16000},
        {"high": 110, "low": 104, "close": 107, "ts": 17000},
        {"high": 106, "low": 101, "close": 103, "ts": 18000}, # SL2 (HL)
        {"high": 109, "low": 103, "close": 107, "ts": 19000},
        {"high": 112, "low": 106, "close": 110, "ts": 20000},
        {"high": 115, "low": 109, "close": 113, "ts": 21000},
    ]


def test_detect_fractal_pivots_uptrend():
    candles = _make_uptrend_candles()
    highs, lows = detect_fractal_pivots(candles, lookback=3, max_pivots=3)
    assert len(highs) >= 2
    assert len(lows) >= 2
    assert highs[0].price >= highs[1].price  # HH
    assert lows[0].price >= lows[1].price    # HL
    assert highs[0].timestamp > highs[1].timestamp


def test_detect_fractal_pivots_max_3():
    candles = _make_uptrend_candles()
    highs, lows = detect_fractal_pivots(candles, lookback=3, max_pivots=3)
    assert len(highs) <= 3
    assert len(lows) <= 3


def test_detect_fractal_pivots_empty():
    highs, lows = detect_fractal_pivots([], lookback=3, max_pivots=3)
    assert highs == []
    assert lows == []


def test_detect_fractal_pivots_insufficient():
    candles = [{"high": 100, "low": 95, "close": 98, "ts": i} for i in range(5)]
    highs, lows = detect_fractal_pivots(candles, lookback=3, max_pivots=3)
    assert highs == []
    assert lows == []


# ---------------------------------------------------------------------------
# Task 3: compute_multi_tf_swings
# ---------------------------------------------------------------------------

def test_compute_multi_tf_swings_uptrend():
    bars = _make_1m_bars(days=10, base_price=19000.0)
    for i, bar in enumerate(bars):
        trend = i * 0.01
        bar["high"] += trend
        bar["low"] += trend
        bar["close"] = bar.get("close", bar["high"] - 2) + trend
        bar["open"] = bar.get("open", bar["low"] + 2) + trend
    result = compute_multi_tf_swings(bars)
    assert isinstance(result, SwingStructure)
    assert result.daily.timeframe == "daily"
    assert result.daily.structure in ("uptrend", "downtrend", "ranging")
    assert len(result.daily.swing_highs) <= 3
    assert len(result.daily.swing_lows) <= 3
    assert -1.0 <= result.trend_alignment <= 1.0


def test_compute_multi_tf_swings_graceful_degradation():
    bars = _make_1m_bars(days=5)
    result = compute_multi_tf_swings(bars)
    assert result.daily.timeframe == "daily"
    assert result.weekly.structure == "ranging"
    assert result.monthly.structure == "ranging"


def test_compute_multi_tf_swings_empty():
    result = compute_multi_tf_swings([])
    assert result.daily.structure == "ranging"
    assert result.weekly.structure == "ranging"
    assert result.monthly.structure == "ranging"
    assert result.trend_alignment == 0.0


def test_compute_multi_tf_swings_timeframe_labels():
    bars = _make_1m_bars(days=10)
    result = compute_multi_tf_swings(bars)
    for sh in result.daily.swing_highs:
        assert sh.timeframe == "daily"
    for sl in result.daily.swing_lows:
        assert sl.timeframe == "daily"


# ---------------------------------------------------------------------------
# Task 5: extract_structure_features with swing data
# ---------------------------------------------------------------------------

import numpy as np
from src.rl.features.structure_features import extract_structure_features


def _make_test_swing_structure() -> SwingStructure:
    return SwingStructure(
        daily=TimeframeSwings(
            timeframe="daily", structure="uptrend",
            swing_highs=[
                SwingLevel(price=19500, timestamp=1000, type="swing_high", timeframe="daily"),
                SwingLevel(price=19300, timestamp=800, type="swing_high", timeframe="daily"),
            ],
            swing_lows=[
                SwingLevel(price=19200, timestamp=900, type="swing_low", timeframe="daily"),
                SwingLevel(price=19100, timestamp=700, type="swing_low", timeframe="daily"),
            ],
        ),
        weekly=TimeframeSwings(
            timeframe="weekly", structure="uptrend",
            swing_highs=[SwingLevel(price=19600, timestamp=500, type="swing_high", timeframe="weekly")],
            swing_lows=[SwingLevel(price=18900, timestamp=400, type="swing_low", timeframe="weekly")],
        ),
        monthly=TimeframeSwings(
            timeframe="monthly", structure="ranging",
            swing_highs=[], swing_lows=[],
        ),
        trend_alignment=0.67,
    )


def test_structure_features_with_swings():
    """Structure features should be 32 elements with swing data."""
    swing = _make_test_swing_structure()
    feats = extract_structure_features(
        price=19400.0,
        vwap_bands=None,
        volume_profile=None,
        session_levels=None,
        session_context=None,
        swing_structure=swing,
    )
    assert feats.shape == (32,)
    assert feats[23] == 1.0   # swing_trend_d = uptrend = +1
    assert feats[24] == 1.0   # swing_trend_w = uptrend = +1
    assert feats[25] == 0.0   # swing_trend_m = ranging = 0
    assert 0.0 <= feats[29] <= 1.0  # swing_pos_d
    assert all(np.isfinite(feats))


def test_structure_features_without_swings():
    """Without swing data, features 23-31 should be zeros."""
    feats = extract_structure_features(
        price=19400.0,
        vwap_bands=None,
        volume_profile=None,
        session_levels=None,
        session_context=None,
        swing_structure=None,
    )
    assert feats.shape == (32,)
    assert all(feats[23:32] == 0.0)
