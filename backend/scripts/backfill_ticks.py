"""Backfill historical tick parquets into market_trades Postgres table."""
import pandas as pd
from pathlib import Path
from sqlalchemy import create_engine, text
import time
import os

db_url = os.environ.get(
    "MARKET_DATABASE_URL",
    "postgresql://firev:firev2026secure@postgres:5432/market",
).replace("+asyncpg", "")

engine = create_engine(db_url)
ticks_dir = Path("/app/data/rl/ticks")
files = sorted(ticks_dir.glob("NQ_*.parquet"))

with engine.connect() as c:
    r = c.execute(text("SELECT MIN(ts), MAX(ts), COUNT(*) FROM market_trades")).fetchone()
    print(f"DB has: {r[2]:,} rows, range: {r[0]} to {r[1]}", flush=True)

total_inserted = 0
for f in files:
    t0 = time.time()
    df = pd.read_parquet(f)
    df = df.rename(columns={"timestamp": "ts"})
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_localize(None)
    df["symbol"] = "NQ"

    ts_min, ts_max = df["ts"].min(), df["ts"].max()
    with engine.connect() as c:
        existing = c.execute(text(
            "SELECT COUNT(*) FROM market_trades WHERE ts >= :lo AND ts <= :hi"
        ), {"lo": ts_min, "hi": ts_max}).scalar()

    if existing > len(df) * 0.9:
        print(f"  {f.name}: already loaded ({existing:,}), skip", flush=True)
        continue

    chunk_size = 100_000
    inserted = 0
    for i in range(0, len(df), chunk_size):
        chunk = df.iloc[i:i + chunk_size][["symbol", "ts", "price", "size", "side"]]
        chunk.to_sql("market_trades", engine, if_exists="append", index=False, method="multi")
        inserted += len(chunk)

    elapsed = time.time() - t0
    total_inserted += inserted
    print(f"  {f.name}: {inserted:,} rows in {elapsed:.0f}s", flush=True)

print(f"\nBackfill complete: {total_inserted:,} rows inserted", flush=True)
