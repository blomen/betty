import time
from pathlib import Path

import pandas as pd
import pytest

from src.market_data.l1_persistence import L1ParquetWriter


@pytest.fixture
def tmp_dir(tmp_path) -> Path:
    return tmp_path / "l1"


def test_writer_creates_directory(tmp_dir):
    writer = L1ParquetWriter(out_dir=tmp_dir)
    writer.close()
    assert tmp_dir.exists()


def test_writer_appends_records_and_flushes_to_parquet(tmp_dir):
    writer = L1ParquetWriter(out_dir=tmp_dir, flush_interval_s=0.0)
    now = time.time()
    writer.record(bid=25000.0, ask=25000.25, bid_size=10, ask_size=8, ts=now)
    writer.record(bid=25000.25, ask=25000.5, bid_size=5, ask_size=12, ts=now + 0.1)
    writer.flush()
    writer.close()

    files = sorted(tmp_dir.rglob("*.parquet"))
    assert len(files) >= 1
    df = pd.read_parquet(files[0])
    assert list(df.columns) == ["ts", "bid", "ask", "bid_size", "ask_size"]
    assert len(df) == 2


def test_writer_partitions_by_utc_date(tmp_dir, monkeypatch):
    """Files should be partitioned: <out_dir>/YYYY-MM-DD/NQ_HH.parquet"""
    from datetime import datetime, timezone

    fake_now = datetime(2026, 5, 17, 14, 30, tzinfo=timezone.utc).timestamp()
    writer = L1ParquetWriter(out_dir=tmp_dir, flush_interval_s=0.0)
    writer.record(bid=25000.0, ask=25000.25, bid_size=10, ask_size=8, ts=fake_now)
    writer.flush()
    writer.close()

    expected_dir = tmp_dir / "2026-05-17"
    assert expected_dir.exists()
    files = list(expected_dir.glob("*.parquet"))
    assert len(files) == 1
    assert "14" in files[0].name  # hour partition


def test_writer_buffers_until_flush_interval(tmp_dir):
    """With flush_interval_s=10, calling record() shouldn't write to disk."""
    writer = L1ParquetWriter(out_dir=tmp_dir, flush_interval_s=10.0)
    now = time.time()
    writer.record(bid=25000.0, ask=25000.25, bid_size=10, ask_size=8, ts=now)
    # Don't call flush — no files yet
    files = list(tmp_dir.rglob("*.parquet"))
    assert len(files) == 0
    writer.close()  # close should always flush


def test_close_flushes_remaining_buffer(tmp_dir):
    writer = L1ParquetWriter(out_dir=tmp_dir, flush_interval_s=3600.0)
    now = time.time()
    writer.record(bid=25000.0, ask=25000.25, bid_size=10, ask_size=8, ts=now)
    writer.close()
    files = list(tmp_dir.rglob("*.parquet"))
    assert len(files) == 1
