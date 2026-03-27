"""Backfill candle gaps in market_candles from Databento historical.

Scans for mid-series gaps and fetches missing 1m/5m bars.
Run from backend/ directory:
    python -m scripts.backfill_candle_gaps
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Load .env
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from src.db.models import get_session, MarketCandle
from src.repositories.market_repo import MarketRepo
from src.market_data.databento_provider import DabentoProvider
from src.config.trading_loader import get_market_data_config


async def main():
    symbol = "NQ"
    now = datetime.now(timezone.utc)
    lookback_start = now - timedelta(hours=24)
    fetch_end = now - timedelta(minutes=15)  # Databento 15m delay

    config = get_market_data_config()
    db_symbol = config.get("symbol", "NQ.v.0")
    provider = DabentoProvider(config)

    db = get_session()
    repo = MarketRepo(db)

    for interval in ("1m", "5m"):
        bucket_s = 60 if interval == "1m" else 300
        max_gap = bucket_s * 3

        rows = repo.get_candles(symbol, interval, lookback_start, now)
        print(f"\n[{interval}] Scanned {len(rows)} candles in last 24h")

        if len(rows) < 2:
            print(f"  Not enough data to detect gaps")
            continue

        # Find gaps
        gaps = []
        for i in range(1, len(rows)):
            ts_prev = rows[i - 1].ts if rows[i - 1].ts.tzinfo else rows[i - 1].ts.replace(tzinfo=timezone.utc)
            ts_curr = rows[i].ts if rows[i].ts.tzinfo else rows[i].ts.replace(tzinfo=timezone.utc)
            diff = (ts_curr - ts_prev).total_seconds()
            if diff > max_gap and ts_curr < fetch_end:
                gaps.append((ts_prev, ts_curr, diff))
                print(f"  GAP: {ts_prev} -> {ts_curr} ({diff/60:.0f} min)")

        if not gaps:
            print(f"  No gaps found")
            continue

        # Backfill each gap
        for gap_start, gap_end, gap_s in gaps:
            print(f"  Backfilling {gap_start} -> {gap_end} ...")
            try:
                bars = await asyncio.wait_for(
                    provider.get_bars(db_symbol, interval, gap_start, gap_end),
                    timeout=120.0,
                )
                print(f"    Fetched {len(bars)} bars from Databento")
                if bars:
                    write_db = get_session()
                    try:
                        count = MarketRepo(write_db).bulk_insert_candles(symbol, interval, bars)
                        print(f"    Inserted {count} new candles")
                    finally:
                        write_db.close()
                else:
                    print(f"    No data returned from Databento")
            except Exception as e:
                print(f"    ERROR: {e}")

    db.close()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
