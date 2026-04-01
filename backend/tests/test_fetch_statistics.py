# backend/tests/test_fetch_statistics.py
"""Tests for historical statistics data fetcher."""
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from pathlib import Path


def test_fetch_statistics_history_saves_parquet(tmp_path):
    """Test that fetch_statistics_history creates a valid parquet file."""
    from src.rl.data.fetcher import fetch_statistics_history

    # Mock Databento client
    mock_client = MagicMock()

    # Create fake StatMsg-like records
    class FakeRecord:
        def __init__(self, stat_type_val, quantity, price, ts_ref, ts_event):
            self.stat_type = stat_type_val
            self.quantity = quantity
            self.price = price
            self.ts_ref = ts_ref
            self.ts_event = ts_event
            self.hd = MagicMock(ts_event=ts_event)

    from databento_dbn import StatType

    # Two days of data
    day1_ns = int(datetime(2025, 1, 6, 12, 30, tzinfo=timezone.utc).timestamp() * 1e9)
    day2_ns = int(datetime(2025, 1, 7, 12, 30, tzinfo=timezone.utc).timestamp() * 1e9)
    ref1_ns = int(datetime(2025, 1, 3, 0, 0, tzinfo=timezone.utc).timestamp() * 1e9)
    ref2_ns = int(datetime(2025, 1, 6, 0, 0, tzinfo=timezone.utc).timestamp() * 1e9)

    records = [
        FakeRecord(StatType.OPEN_INTEREST, 250000, 0, ref1_ns, day1_ns),
        FakeRecord(StatType.CLEARED_VOLUME, 400000, 0, ref1_ns, day1_ns),
        FakeRecord(StatType.BLOCK_VOLUME, 20000, 0, ref1_ns, day1_ns),
        FakeRecord(StatType.SETTLEMENT_PRICE, 0, int(19050.0 * 1e9), ref1_ns, day1_ns),
        FakeRecord(StatType.OPEN_INTEREST, 260000, 0, ref2_ns, day2_ns),
        FakeRecord(StatType.CLEARED_VOLUME, 350000, 0, ref2_ns, day2_ns),
        FakeRecord(StatType.BLOCK_VOLUME, 15000, 0, ref2_ns, day2_ns),
        FakeRecord(StatType.SETTLEMENT_PRICE, 0, int(19100.0 * 1e9), ref2_ns, day2_ns),
    ]
    mock_client.timeseries.get_range.return_value = records

    with patch("databento.Historical", return_value=mock_client):
        result = fetch_statistics_history(
            start=datetime(2025, 1, 1, tzinfo=timezone.utc),
            end=datetime(2025, 1, 31, tzinfo=timezone.utc),
            output_dir=tmp_path,
            api_key="test-key",
        )

    assert result is not None
    assert result.exists()

    import pandas as pd
    df = pd.read_parquet(result)
    assert len(df) == 2
    assert "open_interest" in df.columns
    assert "settlement_price" in df.columns
    assert "oi_change" in df.columns
    assert df["open_interest"].iloc[0] == 250000
    assert df["oi_change"].iloc[1] == 10000  # 260000 - 250000
