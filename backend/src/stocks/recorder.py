"""Record live TopstepX market data to PostgreSQL for replay training."""
from __future__ import annotations

import logging
import time
import threading
from collections import deque

log = logging.getLogger(__name__)

# Batch settings — flush every N records or every M seconds
TICK_BATCH_SIZE = 500
DEPTH_BATCH_SIZE = 200
FLUSH_INTERVAL_S = 5.0


class MarketRecorder:
    """Batched writer for ticks and L2 depth to PostgreSQL.

    Accumulates records in memory and flushes periodically
    to avoid per-tick DB overhead.
    """

    def __init__(self, db_session_factory) -> None:
        self._db_factory = db_session_factory
        self._tick_buffer: deque[dict] = deque()
        self._depth_buffer: deque[dict] = deque()
        self._lock = threading.Lock()
        self._last_flush = time.time()
        self._running = False
        self._flush_thread: threading.Thread | None = None

    def start(self) -> None:
        """Start background flush thread."""
        self._running = True
        self._flush_thread = threading.Thread(
            target=self._flush_loop, daemon=True, name="market-recorder",
        )
        self._flush_thread.start()
        log.info("MarketRecorder started")

    def stop(self) -> None:
        """Flush remaining data and stop."""
        self._running = False
        self._flush_all()
        log.info("MarketRecorder stopped")

    def record_tick(self, price: float, size: int, ts: float) -> None:
        """Buffer a tick for batch insert."""
        from datetime import datetime, timezone
        with self._lock:
            self._tick_buffer.append({
                "price": price, "size": size,
                "ts": datetime.fromtimestamp(ts, tz=timezone.utc),
            })

    def record_depth(self, depth: dict) -> None:
        """Buffer an L2 depth update for batch insert."""
        from datetime import datetime, timezone
        ts_raw = depth.get("timestamp")
        if ts_raw:
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except Exception:
                ts = datetime.now(timezone.utc)
        else:
            ts = datetime.now(timezone.utc)
        with self._lock:
            self._depth_buffer.append({
                "price": float(depth.get("price", 0)),
                "volume": int(depth.get("volume", 0)),
                "current_volume": int(depth.get("currentVolume", 0)),
                "side": "bid" if depth.get("type") == 0 else "ask",
                "ts": ts,
            })

    def _flush_loop(self) -> None:
        """Periodic flush in background thread."""
        while self._running:
            time.sleep(FLUSH_INTERVAL_S)
            self._flush_all()

    def _flush_all(self) -> None:
        """Flush both buffers to DB."""
        with self._lock:
            ticks = list(self._tick_buffer)
            depths = list(self._depth_buffer)
            self._tick_buffer.clear()
            self._depth_buffer.clear()

        if not ticks and not depths:
            return

        try:
            db = self._db_factory()
            try:
                if ticks:
                    self._insert_ticks(db, ticks)
                if depths:
                    self._insert_depths(db, depths)
                db.commit()
            finally:
                db.close()
        except Exception:
            log.exception("MarketRecorder flush failed (%d ticks, %d depths)", len(ticks), len(depths))

    def _insert_ticks(self, db, ticks: list[dict]) -> None:
        """Batch insert ticks."""
        from sqlalchemy import text
        db.execute(
            text("""
                INSERT INTO recorded_ticks (symbol, price, size, ts)
                VALUES (:symbol, :price, :size, :ts)
            """),
            [{"symbol": "NQ", "price": t["price"], "size": t["size"], "ts": t["ts"]} for t in ticks],
        )
        log.debug("Flushed %d ticks", len(ticks))

    def _insert_depths(self, db, depths: list[dict]) -> None:
        """Batch insert depth snapshots."""
        from sqlalchemy import text
        db.execute(
            text("""
                INSERT INTO recorded_depth (symbol, price, volume, current_volume, side, ts)
                VALUES (:symbol, :price, :volume, :current_volume, :side, :ts)
            """),
            [{"symbol": "NQ", **d} for d in depths],
        )
        log.debug("Flushed %d depth records", len(depths))
