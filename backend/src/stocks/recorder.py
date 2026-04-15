"""Record live TopstepX market data to PostgreSQL for replay training."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

log = logging.getLogger(__name__)

# Batch settings — flush every N records or every M seconds
TICK_BATCH_SIZE = 500
DEPTH_BATCH_SIZE = 200
FLUSH_INTERVAL_S = 5.0

# Resilience settings
MAX_CONSECUTIVE_FAILURES = 5  # disable after this many failures in a row
MAX_BUFFER_SIZE = 50_000  # cap re-queued records to ~50k to bound memory
BACKOFF_BASE_S = 5.0  # base sleep between flushes; doubles on failure


class MarketRecorder:
    """Batched writer for ticks and L2 depth to PostgreSQL.

    Accumulates records in memory and flushes periodically
    to avoid per-tick DB overhead.  Auto-disables after repeated
    DB failures to stop log spam and memory growth.
    """

    def __init__(self, db_session_factory) -> None:
        self._db_factory = db_session_factory
        self._tick_buffer: deque[dict] = deque()
        self._depth_buffer: deque[dict] = deque()
        self._lock = threading.Lock()
        self._last_flush = time.time()
        self._running = False
        self._disabled = False
        self._consecutive_failures = 0
        self._flush_thread: threading.Thread | None = None

    @staticmethod
    def check_connectivity(db_session_factory) -> bool:
        """Test DB connectivity. Returns True if a session can be opened."""
        try:
            db = db_session_factory()
            try:
                from sqlalchemy import text

                db.execute(text("SELECT 1"))
                return True
            finally:
                db.close()
        except Exception:
            return False

    def start(self) -> None:
        """Start background flush thread."""
        self._running = True
        self._flush_thread = threading.Thread(
            target=self._flush_loop,
            daemon=True,
            name="market-recorder",
        )
        self._flush_thread.start()
        log.info("MarketRecorder started")

    def stop(self) -> None:
        """Flush remaining data and stop."""
        self._running = False
        if not self._disabled:
            self._flush_all()
        log.info("MarketRecorder stopped")

    def record_tick(self, price: float, size: int, ts: float) -> None:
        """Buffer a tick for batch insert."""
        if self._disabled:
            return
        from datetime import datetime, timezone

        with self._lock:
            if len(self._tick_buffer) < MAX_BUFFER_SIZE:
                self._tick_buffer.append(
                    {
                        "price": price,
                        "size": size,
                        "ts": datetime.fromtimestamp(ts, tz=timezone.utc),
                    }
                )

    def record_depth(self, depth: dict) -> None:
        """Buffer an L2 depth update for batch insert."""
        if self._disabled:
            return
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
            if len(self._depth_buffer) < MAX_BUFFER_SIZE:
                self._depth_buffer.append(
                    {
                        "price": float(depth.get("price", 0)),
                        "volume": int(depth.get("volume", 0)),
                        "current_volume": int(depth.get("currentVolume", 0)),
                        "side": "bid" if depth.get("type") == 0 else "ask",
                        "ts": ts,
                    }
                )

    def _flush_loop(self) -> None:
        """Periodic flush in background thread with exponential backoff."""
        sleep_s = BACKOFF_BASE_S
        while self._running:
            if self._disabled:
                time.sleep(60)
                continue
            time.sleep(sleep_s)
            ok = self._flush_all()
            if ok:
                sleep_s = BACKOFF_BASE_S  # reset on success
            else:
                sleep_s = min(sleep_s * 2, 60.0)  # backoff, cap at 60s

    def _flush_all(self) -> bool:
        """Flush both buffers to DB. Returns True on success."""
        with self._lock:
            ticks = list(self._tick_buffer)
            depths = list(self._depth_buffer)
            self._tick_buffer.clear()
            self._depth_buffer.clear()

        if not ticks and not depths:
            return True

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
            if self._consecutive_failures > 0:
                log.info("MarketRecorder reconnected after %d failures", self._consecutive_failures)
            self._consecutive_failures = 0
            return True
        except Exception:
            self._consecutive_failures += 1
            # Re-queue records so they aren't lost (respect cap)
            with self._lock:
                requeue_ticks = ticks[: MAX_BUFFER_SIZE - len(self._tick_buffer)]
                requeue_depths = depths[: MAX_BUFFER_SIZE - len(self._depth_buffer)]
                self._tick_buffer.extendleft(reversed(requeue_ticks))
                self._depth_buffer.extendleft(reversed(requeue_depths))

            if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                log.error(
                    "MarketRecorder disabled after %d consecutive failures — "
                    "DB unreachable, ticks will not be recorded. "
                    "Restart firevstocks to retry.",
                    self._consecutive_failures,
                )
                self._disabled = True
                with self._lock:
                    self._tick_buffer.clear()
                    self._depth_buffer.clear()
            elif self._consecutive_failures == 1:
                # Log full trace only on first failure
                log.warning(
                    "MarketRecorder flush failed (%d ticks, %d depths) — retrying with backoff", len(ticks), len(depths)
                )
            else:
                log.warning(
                    "MarketRecorder flush failed (%d/%d) — attempt %d/%d",
                    len(ticks),
                    len(depths),
                    self._consecutive_failures,
                    MAX_CONSECUTIVE_FAILURES,
                )
            return False

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
