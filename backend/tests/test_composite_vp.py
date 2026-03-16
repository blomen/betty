"""Tests for weekly/monthly composite volume profile levels."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import date, datetime, timedelta
from src.market_data.levels import compute_volume_profile, VolumeProfile


def _make_bars(n_days: int, base_price: float = 20000, vol: int = 100) -> list[dict]:
    """Create synthetic bar dicts for n trading days of 1-hour RTH bars (7 per day)."""
    bars = []
    for d in range(n_days):
        for h in range(7):
            price = base_price + d * 10 + h
            bars.append({"high": price + 2, "low": price - 2, "close": price, "volume": vol})
    return bars


def test_compute_volume_profile_from_bar_dicts():
    """Verify compute_volume_profile works with bar-derived trade dicts."""
    bars = _make_bars(5)
    trades = [{"price": b["close"], "size": b.get("volume", 1)} for b in bars]
    vp = compute_volume_profile(trades)
    assert vp.poc > 0
    assert vp.vah >= vp.poc >= vp.val
    assert vp.val > 0


def test_weekly_vp_minimum_threshold():
    """Weekly VP should require >= 780 bars (~2 days of 1-min data)."""
    short_bars = _make_bars(1)  # Only 7 bars (1 day of 1h)
    assert len(short_bars) < 780  # Below threshold

    adequate_bars = [{"close": 20000 + i, "volume": 100} for i in range(800)]
    assert len(adequate_bars) >= 780  # Above threshold


def test_monthly_vp_minimum_threshold():
    """Monthly VP should require >= 35 bars (~5 days of 1h data)."""
    short_bars = _make_bars(4)  # 28 bars
    assert len(short_bars) < 35  # Below threshold

    adequate_bars = _make_bars(5)  # 35 bars
    assert len(adequate_bars) >= 35  # At threshold


def test_session_levels_to_rows_includes_weekly_vp():
    """Weekly VP levels should appear in level rows when provided."""
    from src.services.market_service import MarketService
    from src.market_data.levels import SessionLevels

    levels = SessionLevels(pdh=20100, pdl=19900)
    session_data = {"poc": 20000, "vah": 20050, "val": 19950, "vwap": 20010}
    weekly_vp = VolumeProfile(poc=20020, vah=20080, val=19920)

    rows = MarketService._session_levels_to_rows(levels, session_data, weekly_vp=weekly_vp)

    types = [r["level_type"] for r in rows]
    assert "weekly_poc" in types
    assert "weekly_vah" in types
    assert "weekly_val" in types

    weekly_poc_row = next(r for r in rows if r["level_type"] == "weekly_poc")
    assert weekly_poc_row["price_low"] == 20020
    assert weekly_poc_row["session"] == "weekly"
    assert weekly_poc_row["direction"] is None

    weekly_vah_row = next(r for r in rows if r["level_type"] == "weekly_vah")
    assert weekly_vah_row["direction"] == "resistance"


def test_session_levels_to_rows_includes_monthly_vp():
    """Monthly VP levels should appear in level rows when provided."""
    from src.services.market_service import MarketService
    from src.market_data.levels import SessionLevels

    levels = SessionLevels()
    session_data = {"poc": 20000, "vah": 20050, "val": 19950, "vwap": 20010}
    monthly_vp = VolumeProfile(poc=19800, vah=20200, val=19400)

    rows = MarketService._session_levels_to_rows(levels, session_data, monthly_vp=monthly_vp)

    types = [r["level_type"] for r in rows]
    assert "monthly_poc" in types
    assert "monthly_vah" in types
    assert "monthly_val" in types

    monthly_val_row = next(r for r in rows if r["level_type"] == "monthly_val")
    assert monthly_val_row["price_low"] == 19400
    assert monthly_val_row["session"] == "monthly"
    assert monthly_val_row["direction"] == "support"


def test_session_levels_to_rows_no_composite_when_none():
    """No composite levels when VPs are None."""
    from src.services.market_service import MarketService
    from src.market_data.levels import SessionLevels

    levels = SessionLevels()
    session_data = {"poc": 20000, "vah": 20050, "val": 19950, "vwap": 20010}

    rows = MarketService._session_levels_to_rows(levels, session_data)

    types = [r["level_type"] for r in rows]
    assert "weekly_poc" not in types
    assert "monthly_poc" not in types
