"""Test candle snapshot extraction for M6 temporal patterns."""
from datetime import datetime, timezone
from src.market_data.orderflow import CandleFlow


def _make_candle(minute: int, delta: int = 100, volume: int = 4000) -> CandleFlow:
    return CandleFlow(
        ts=datetime(2026, 3, 12, 15, minute, tzinfo=timezone.utc),
        open=21500.0, high=21505.0, low=21498.0, close=21503.0,
        volume=volume, buy_volume=volume // 2 + delta // 2,
        sell_volume=volume // 2 - delta // 2,
        delta=delta, tick_count=1800, spread=28,
    )


def test_snapshot_extracts_last_20():
    from src.ml.features.candle_features import snapshot_candles
    candles = [_make_candle(i) for i in range(30)]
    result = snapshot_candles(candles, vwap=21501.0, poc=21504.0)
    assert len(result) == 20


def test_snapshot_with_fewer_than_20():
    from src.ml.features.candle_features import snapshot_candles
    candles = [_make_candle(i) for i in range(5)]
    result = snapshot_candles(candles, vwap=21501.0, poc=21504.0)
    assert len(result) == 5


def test_snapshot_fields():
    from src.ml.features.candle_features import snapshot_candles
    candles = [_make_candle(i, delta=200 + i * 10, volume=4000 + i * 100) for i in range(20)]
    result = snapshot_candles(candles, vwap=21501.0, poc=21504.0)
    c = result[0]
    assert "delta" in c
    assert "delta_pct" in c
    assert "volume" in c
    assert "body_ratio" in c
    assert "vwap_distance_ticks" in c
    assert "poc_distance_ticks" in c
