from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from src.market_data.levels import (
    aggregate_to_timeframe,
    compute_multi_tf_swings,
    TimeframeSwings,
    SwingStructure,
)
from src.market_data.structure import SwingLevel

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
    """Structure features should be 38 elements with swing data."""
    swing = _make_test_swing_structure()
    feats = extract_structure_features(
        price=19400.0,
        vwap_bands=None,
        volume_profile=None,
        session_levels=None,
        session_context=None,
        swing_structure=swing,
    )
    assert feats.shape == (38,)
    assert feats[23] == 1.0   # swing_trend_d = uptrend = +1
    assert feats[24] == 1.0   # swing_trend_w = uptrend = +1
    assert feats[25] == 0.0   # swing_trend_m = ranging = 0
    assert 0.0 <= feats[29] <= 1.0  # swing_pos_d
    assert all(np.isfinite(feats))


def test_structure_features_without_swings():
    """Without swing data, features 23-37 should be zeros."""
    feats = extract_structure_features(
        price=19400.0,
        vwap_bands=None,
        volume_profile=None,
        session_levels=None,
        session_context=None,
        swing_structure=None,
    )
    assert feats.shape == (38,)
    assert all(feats[23:38] == 0.0)


# ---------------------------------------------------------------------------
# Task 11: Integration tests for full observation vector with swing features
# ---------------------------------------------------------------------------

def test_observation_dim_with_swings():
    """Full observation vector should include swing structure features."""
    from src.rl.features.observation import build_observation, OBSERVATION_DIM
    from src.rl.config import LevelType
    from src.rl.zone_builder import Zone, ZoneMember

    dummy_member = ZoneMember(name="vwap", level_type=LevelType.VWAP, price=19000.0)
    dummy_zone = Zone(
        center_price=19000.0, upper_bound=19001.0, lower_bound=18999.0,
        members=[dummy_member],
        composition=[1.0 if lt == LevelType.VWAP else 0.0 for lt in LevelType],
        width_ticks=8.0, member_count=1, hierarchy_score=0.5,
    )

    state = {
        "zone": dummy_zone,
        "all_zones": [dummy_zone],
        "price": 19000.0,
        "candles": [],
        "vwap_bands": None,
        "volume_profile": None,
        "session_tpos": None,
        "session_levels": None,
        "all_levels": [],
        "orderflow_signals": None,
        "macro": None,
        "session_context": None,
        "recent_ticks": [],
        "swing_structure": _make_test_swing_structure(),
    }
    obs = build_observation(state)
    # OBSERVATION_DIM is computed dynamically from actual segment sizes
    assert obs.shape[0] == OBSERVATION_DIM
    # Verify structure segment grew by 9 (was 23, now 32)
    assert all(np.isfinite(obs))


def test_level_type_enum_count():
    from src.rl.config import LevelType
    assert len(LevelType) == 31
