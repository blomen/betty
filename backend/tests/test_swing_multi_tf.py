from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from src.market_data.levels import aggregate_to_timeframe, SwingLevel, TimeframeSwings, SwingStructure

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
