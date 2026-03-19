"""Tests for volume profile computation and session level rows."""

from src.market_data.levels import compute_volume_profile


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


def test_session_levels_to_rows_daily_only():
    """Session level rows should contain daily VP levels, no composite."""
    from src.services.market_service import MarketService
    from src.market_data.levels import SessionLevels

    levels = SessionLevels(pdh=20100, pdl=19900)
    session_data = {"poc": 20000, "vah": 20050, "val": 19950, "vwap": 20010}

    rows = MarketService._session_levels_to_rows(levels, session_data)

    types = [r["level_type"] for r in rows]
    assert "poc" in types
    assert "vah" in types
    assert "val" in types
    assert "pdh" in types
    assert "pdl" in types
    # No composite VP levels
    assert "weekly_poc" not in types
    assert "monthly_poc" not in types
