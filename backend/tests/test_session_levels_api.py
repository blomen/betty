"""Test session levels endpoint returns correct structure."""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from src.services.market_service import MarketService


class FakeCandle:
    def __init__(self, ts, h, l, c=0, o=0, v=100):
        self.ts = ts
        self.h = h
        self.l = l
        self.c = c or h
        self.o = o or l
        self.v = v


@pytest.mark.asyncio
async def test_session_levels_returns_per_day_structure():
    """Verify get_session_levels returns levels with time boundaries."""
    from zoneinfo import ZoneInfo
    _CET = ZoneInfo("Europe/Stockholm")

    # Create fake 1m candles spanning 2 days
    base = datetime(2026, 3, 18, 0, 0, tzinfo=_CET)
    candles = []
    for hour in range(0, 22):
        ts = (base + timedelta(hours=hour)).astimezone(timezone.utc)
        candles.append(FakeCandle(ts=ts, h=21500 + hour * 10, l=21490 + hour * 10))

    base2 = datetime(2026, 3, 19, 0, 0, tzinfo=_CET)
    for hour in range(0, 22):
        ts = (base2 + timedelta(hours=hour)).astimezone(timezone.utc)
        candles.append(FakeCandle(ts=ts, h=21600 + hour * 10, l=21590 + hour * 10))

    mock_db = MagicMock()
    svc = MarketService(mock_db)

    with patch.object(svc.repo, 'get_candles', return_value=candles):
        result = await svc.get_session_levels("NQ", days=2)

    assert "days" in result
    assert len(result["days"]) == 2

    day = result["days"][0]  # most recent
    assert day["date"] == "2026-03-19"
    assert "pdh" in day
    assert "ib_high" in day
    assert "tokyo_high" in day
    assert "london_high" in day
    # Time boundaries are present
    assert "tokyo_start" in day
    assert "day_end" in day
    # Time boundaries are integers (epochs)
    assert isinstance(day["tokyo_start"], int)
