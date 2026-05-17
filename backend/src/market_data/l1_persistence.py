"""Append-only L1 quote parquet writer.

Partitioning: <out_dir>/YYYY-MM-DD/NQ_HH.parquet
- One directory per UTC date
- One file per UTC hour
- Each file is rewritten on every flush (full rewrite, not append)
  — pyarrow doesn't support true append on a single parquet file, so we
  buffer in-memory and rewrite the hour file on each flush. NQ generates
  ~50k quote updates/hour which is small enough that rewriting is fine.

Forward-going only — there's no backfill source. Every minute of L1
data missed = a minute of OF training data the model won't have.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

log = logging.getLogger(__name__)


class L1ParquetWriter:
    def __init__(
        self,
        out_dir: Path | str,
        flush_interval_s: float = 60.0,
    ) -> None:
        self._out_dir = Path(out_dir)
        self._out_dir.mkdir(parents=True, exist_ok=True)
        self._flush_interval_s = flush_interval_s
        self._buf: list[dict] = []
        self._last_flush_ts: float | None = None  # lazy-init on first record

    def record(
        self,
        bid: float,
        ask: float,
        bid_size: int,
        ask_size: int,
        ts: float,
    ) -> None:
        if self._last_flush_ts is None:
            self._last_flush_ts = ts
        self._buf.append(
            {
                "ts": ts,
                "bid": float(bid),
                "ask": float(ask),
                "bid_size": int(bid_size),
                "ask_size": int(ask_size),
            }
        )
        if ts - self._last_flush_ts >= self._flush_interval_s:
            self.flush()

    def flush(self) -> None:
        if not self._buf:
            return
        last_flushed_ts = self._buf[-1]["ts"]
        try:
            buf_by_hour: dict[Path, list[dict]] = {}
            for rec in self._buf:
                dt = datetime.fromtimestamp(rec["ts"], tz=timezone.utc)
                date_dir = self._out_dir / dt.strftime("%Y-%m-%d")
                date_dir.mkdir(exist_ok=True)
                file = date_dir / f"NQ_{dt.strftime('%H')}.parquet"
                buf_by_hour.setdefault(file, []).append(rec)
            for file, recs in buf_by_hour.items():
                new_df = pd.DataFrame(recs)
                if file.exists():
                    existing = pd.read_parquet(file)
                    df = pd.concat([existing, new_df], ignore_index=True)
                else:
                    df = new_df
                pq.write_table(pa.Table.from_pandas(df, preserve_index=False), file)
            self._buf.clear()
            self._last_flush_ts = last_flushed_ts
        except Exception:
            log.exception("L1ParquetWriter flush failed")

    def close(self) -> None:
        self.flush()
