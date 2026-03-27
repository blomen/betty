"""Databento live stream client for Trades + MBP-1."""
import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from .level_monitor import LevelMonitor

logger = logging.getLogger(__name__)

# Databento historical has ~15 min delay
DATABENTO_HISTORICAL_DELAY_M = 15
# Minimum gap size worth backfilling (seconds)
MIN_GAP_FOR_BACKFILL_S = 120

# Batch flush config
TICK_BATCH_SIZE = 500
TICK_FLUSH_INTERVAL_S = 5.0


@dataclass
class TopOfBook:
    """Current best bid/ask from MBP-1 stream."""
    bid_price: float = 0.0
    bid_size: int = 0
    ask_price: float = 0.0
    ask_size: int = 0
    spread: float = 0.0
    ts: datetime | None = None

    def update(self, bid_px: float, bid_sz: int, ask_px: float, ask_sz: int, ts: datetime):
        self.bid_price = bid_px
        self.bid_size = bid_sz
        self.ask_price = ask_px
        self.ask_size = ask_sz
        self.spread = ask_px - bid_px
        self.ts = ts


@dataclass
class TickBuffer:
    """Circular buffer of recent ticks with running accumulators."""
    max_size: int = 10_000
    ticks: deque = field(default_factory=lambda: deque(maxlen=10_000))
    cvd: int = 0
    delta_1m: int = 0
    last_candle_ts: datetime | None = None

    def add(self, ts: datetime, price: float, size: int, side: str):
        self.ticks.append({"ts": ts, "price": price, "size": size, "side": side})
        delta = size if side == "A" else -size
        self.cvd += delta
        self.delta_1m += delta

    def reset_candle_delta(self):
        d = self.delta_1m
        self.delta_1m = 0
        return d


class TickWriter:
    """Batches ticks and periodically flushes to the market_trades DB table."""

    def __init__(self, db_session_factory: Callable, symbol: str = "NQ"):
        self._db_session_factory = db_session_factory
        self._symbol = symbol
        self._batch: list[dict] = []
        self._flush_task: asyncio.Task | None = None
        self._running = False

    async def start(self):
        self._running = True
        self._flush_task = asyncio.create_task(self._periodic_flush())

    async def stop(self):
        self._running = False
        if self._flush_task:
            self._flush_task.cancel()
            self._flush_task = None
        await self._flush()

    def add(self, ts: datetime, price: float, size: int, side: str):
        self._batch.append({
            "symbol": self._symbol,
            "ts": ts,
            "price": price,
            "size": size,
            "side": side,
        })
        if len(self._batch) >= TICK_BATCH_SIZE:
            asyncio.create_task(self._flush())

    async def _periodic_flush(self):
        while self._running:
            await asyncio.sleep(TICK_FLUSH_INTERVAL_S)
            await self._flush()

    async def _flush(self):
        if not self._batch:
            return
        batch = self._batch
        self._batch = []
        try:
            db = self._db_session_factory()
            try:
                from ..repositories.market_repo import MarketRepo
                repo = MarketRepo(db)
                repo.bulk_insert_trades(batch)
            finally:
                db.close()
            logger.debug("Flushed %d ticks to market_trades", len(batch))
        except Exception as e:
            logger.error("Tick flush failed (%d ticks lost): %s", len(batch), e)

    @staticmethod
    async def prune_old_trades(db_session_factory: Callable, symbol: str = "NQ"):
        """Delete ticks older than current session (midnight UTC)."""
        cutoff = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            db = db_session_factory()
            try:
                from ..repositories.market_repo import MarketRepo
                repo = MarketRepo(db)
                repo.prune_trades(symbol, cutoff)
                logger.info("Pruned market_trades before %s for %s", cutoff, symbol)
            finally:
                db.close()
        except Exception as e:
            logger.error("Trade prune failed: %s", e)


class CandleFlow:
    """Aggregates ticks into a running OHLCV candle for a configurable time bucket."""

    # Reject ticks more than this fraction away from last known price (bad prints)
    MAX_TICK_DEVIATION = 0.01  # 1% — ~240 pts on NQ
    # After this many consecutive rejects, accept the tick (legitimate gap)
    MAX_CONSECUTIVE_REJECTS = 20

    def __init__(self, bucket_seconds: int = 300, emit_interval: float = 5.0):
        self.BUCKET_SECONDS = bucket_seconds
        self.EMIT_INTERVAL = emit_interval
        self._bucket_start: int = 0
        self._o = self._h = self._l = self._c = 0.0
        self._v = 0
        self._dirty = False
        self._last_emit: float = 0.0
        self._consecutive_rejects = 0

    def seed(self, bucket_start: int, o: float, h: float, l: float, c: float, v: int):
        """Seed from a DB candle so the first live update continues rather than resets."""
        self._bucket_start = bucket_start
        self._o = o
        self._h = h
        self._l = l
        self._c = c
        self._v = v

    def _bucket_for(self, epoch: float) -> int:
        return int(epoch) // self.BUCKET_SECONDS * self.BUCKET_SECONDS

    def _is_outlier(self, price: float) -> bool:
        """Reject ticks that deviate too far from last known price (bad prints)."""
        if self._c == 0.0:
            return False  # no reference yet
        if abs(price - self._c) / self._c > self.MAX_TICK_DEVIATION:
            self._consecutive_rejects += 1
            if self._consecutive_rejects >= self.MAX_CONSECUTIVE_REJECTS:
                # Too many rejects in a row — likely a legitimate gap, accept it
                self._consecutive_rejects = 0
                return False
            return True
        self._consecutive_rejects = 0
        return False

    def update(self, price: float, size: int, epoch: float) -> tuple[dict | None, dict | None]:
        """Feed a tick. Returns (emit_event, closed_candle).

        emit_event: periodic live update for the chart (every EMIT_INTERVAL seconds).
        closed_candle: snapshot of the completed bucket when it rolls over (for DB persistence).
        """
        if self._is_outlier(price):
            return None, None  # skip bad print

        bucket = self._bucket_for(epoch)
        closed_candle = None

        if bucket != self._bucket_start:
            # Previous bucket just closed — capture it before resetting
            if self._bucket_start != 0 and self._v > 0:
                closed_candle = self.snapshot()
            self._bucket_start = bucket
            self._o = self._h = self._l = self._c = price
            self._v = size
        else:
            self._h = max(self._h, price)
            self._l = min(self._l, price)
            self._c = price
            self._v += size

        self._dirty = True

        emit = None
        now = time.monotonic()
        if now - self._last_emit >= self.EMIT_INTERVAL and self._dirty:
            self._last_emit = now
            self._dirty = False
            emit = self.snapshot()

        return emit, closed_candle

    def snapshot(self) -> dict:
        return {
            "type": "candle",
            "t": self._bucket_start,
            "o": self._o,
            "h": self._h,
            "l": self._l,
            "c": self._c,
            "v": self._v,
        }


class DatabentoLiveStream:
    """Manages a persistent Databento live subscription (Trades + MBP-1)."""

    _ET_TZ = None  # Lazy-init to avoid per-tick import overhead

    @staticmethod
    def _in_globex(epoch: float) -> bool:
        """Check if timestamp falls within CME Globex hours.

        Globex: Sun 18:00 ET → Fri 17:00 ET, with daily 17:00-18:00 ET halt.
        """
        if DatabentoLiveStream._ET_TZ is None:
            from zoneinfo import ZoneInfo
            DatabentoLiveStream._ET_TZ = ZoneInfo("US/Eastern")
        dt = datetime.fromtimestamp(epoch, tz=DatabentoLiveStream._ET_TZ)
        wd = dt.weekday()
        hour = dt.hour
        if wd == 5:
            return False
        if wd == 4 and hour >= 17:
            return False
        if wd == 6 and hour < 18:
            return False
        if hour == 17:
            return False
        return True

    # Watchdog: reconnect if no records received within this many seconds
    WATCHDOG_TIMEOUT_S = 60

    def __init__(
        self,
        api_key: str,
        dataset: str = "GLBX.MDP3",
        symbol: str = "NQ.v.0",
        db_session_factory: Callable | None = None,
    ):
        self.api_key = api_key
        self.dataset = dataset
        self.symbol = symbol
        self.buffer = TickBuffer()
        self.book = TopOfBook()
        self._candle_flow = CandleFlow()        # 5m candles (persist only)
        self._candle_flow_1m = CandleFlow(bucket_seconds=60, emit_interval=1.0)  # 1m (emit closed + persist)
        self._level_monitor: LevelMonitor | None = None
        self._running = False
        self._task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._last_record_time: float = 0.0  # monotonic timestamp of last record
        self._subscribers: list[asyncio.Queue] = []
        self._db_session_factory = db_session_factory
        self._candle_write_queue: deque[tuple[dict, str]] = deque(maxlen=500)
        self._candle_retry_task: asyncio.Task | None = None
        self._gap_backfill_task: asyncio.Task | None = None
        self._tick_writer: TickWriter | None = None
        if db_session_factory:
            self._tick_writer = TickWriter(db_session_factory, symbol=symbol.split(".")[0])

    async def start(self):
        if self._running:
            return
        self._running = True
        self._last_record_time = time.monotonic()
        # Prune old ticks on startup
        if self._tick_writer:
            await self._tick_writer.start()

        # Run the stream loop in a DEDICATED thread with its own event loop.
        # The Databento SDK's asyncio.Protocol.data_received() processes socket
        # buffers synchronously on the event loop, starving HTTP handlers.
        # By running in a separate thread/loop, the main event loop stays free.
        self._main_loop = asyncio.get_running_loop()
        import threading
        def _run_stream_thread():
            self._stream_thread_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._stream_thread_loop)
            self._stream_thread_loop.run_until_complete(self._stream_loop())
        self._stream_thread = threading.Thread(target=_run_stream_thread, daemon=True, name="databento-stream")
        self._stream_thread.start()

        # Watchdog and candle tasks stay on the main loop (lightweight, infrequent)
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        if self._db_session_factory:
            self._candle_retry_task = asyncio.create_task(self._candle_retry_loop())
            self._gap_backfill_task = asyncio.create_task(self._periodic_gap_backfill_loop())
        logger.info("Databento live stream started for %s (dedicated thread)", self.symbol)

    def _restart_stream_thread(self):
        """Restart the Databento stream in its dedicated thread."""
        self._running = False
        if hasattr(self, '_stream_thread') and self._stream_thread.is_alive():
            self._stream_thread.join(timeout=5)
        self._running = True
        self._last_record_time = time.monotonic()
        import threading
        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._stream_thread_loop = loop
            loop.run_until_complete(self._stream_loop())
        self._stream_thread = threading.Thread(target=_run, daemon=True, name="databento-stream")
        self._stream_thread.start()

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        if self._watchdog_task:
            self._watchdog_task.cancel()
            self._watchdog_task = None
        if self._candle_retry_task:
            self._candle_retry_task.cancel()
            self._candle_retry_task = None
        if self._gap_backfill_task:
            self._gap_backfill_task.cancel()
            self._gap_backfill_task = None
        if self._tick_writer:
            await self._tick_writer.stop()
        logger.info("Databento live stream stopped")

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        if q in self._subscribers:
            self._subscribers.remove(q)

    def set_level_monitor(self, monitor: LevelMonitor) -> None:
        """Attach a level monitor to receive tick callbacks."""
        self._level_monitor = monitor
        monitor.set_tick_buffer(self.buffer)
        monitor.set_candle_flow_source(self._get_recent_candles)

    async def _write_closed_candle(self, candle: dict, interval: str = "5m"):
        """Persist a completed candle bucket to market_candles DB table.

        On failure (e.g. DB locked), queues the candle for retry.
        """
        try:
            db = self._db_session_factory()
            try:
                from ..repositories.market_repo import MarketRepo
                ts = datetime.fromtimestamp(candle["t"], tz=timezone.utc)
                MarketRepo(db).upsert_candle(
                    symbol=self.symbol.split(".")[0],
                    interval=interval,
                    ts=ts,
                    o=candle["o"], h=candle["h"], l=candle["l"], c=candle["c"], v=candle["v"],
                )
            finally:
                db.close()
        except Exception as e:
            self._candle_write_queue.append((candle, interval))
            logger.warning("Failed to persist closed %s candle (queued, %d pending): %s",
                           interval, len(self._candle_write_queue), e)

    async def _candle_retry_loop(self):
        """Periodically retry persisting queued candles that failed due to DB lock."""
        while self._running:
            await asyncio.sleep(30)
            if not self._candle_write_queue:
                continue
            batch = list(self._candle_write_queue)
            self._candle_write_queue.clear()
            written = 0
            try:
                db = self._db_session_factory()
                try:
                    from ..repositories.market_repo import MarketRepo
                    repo = MarketRepo(db)
                    sym = self.symbol.split(".")[0]
                    for candle, interval in batch:
                        ts = datetime.fromtimestamp(candle["t"], tz=timezone.utc)
                        repo.upsert_candle(
                            symbol=sym, interval=interval, ts=ts,
                            o=candle["o"], h=candle["h"], l=candle["l"], c=candle["c"], v=candle["v"],
                        )
                        written += 1
                finally:
                    db.close()
                if written:
                    logger.info("Candle retry: persisted %d/%d queued candles", written, len(batch))
            except Exception as e:
                # Re-queue the ones we didn't write
                for item in batch[written:]:
                    self._candle_write_queue.append(item)
                logger.warning("Candle retry failed after %d/%d: %s", written, len(batch), e)

    async def _watchdog_loop(self):
        """Monitor stream health — reconnect if no records received within timeout.

        Also handles the daily halt transition: during the 17:00-18:00 ET halt
        no records arrive, but we need to reconnect promptly at 18:00 ET rather
        than waiting for the next watchdog cycle to notice.
        """
        was_in_halt = False
        while self._running:
            await asyncio.sleep(self.WATCHDOG_TIMEOUT_S)
            if not self._running:
                break

            now_epoch = time.time()
            in_globex = self._in_globex(now_epoch)

            # Detect halt → open transition: force reconnect immediately
            from zoneinfo import ZoneInfo
            dt_et = datetime.fromtimestamp(now_epoch, tz=ZoneInfo("US/Eastern"))
            in_halt = dt_et.hour == 17
            if was_in_halt and not in_halt and in_globex:
                logger.info("Databento watchdog: post-halt transition (18:00 ET) — forcing reconnect + backfill")
                self._restart_stream_thread()
                self._last_record_time = time.monotonic()
                was_in_halt = False
                continue
            was_in_halt = in_halt

            elapsed = time.monotonic() - self._last_record_time
            if elapsed > self.WATCHDOG_TIMEOUT_S:
                if in_globex:
                    logger.warning(
                        "Databento watchdog: no records for %.0fs (during Globex hours) — reconnecting",
                        elapsed,
                    )
                    self._restart_stream_thread()
                else:
                    logger.debug("Databento watchdog: no records for %.0fs (outside Globex hours — OK)", elapsed)

    def _get_recent_candles(self):
        """Build CandleFlow candles from recent tick buffer for orderflow computation."""
        from .orderflow import build_candle_flow
        ticks = list(self.buffer.ticks)  # deque snapshot
        if len(ticks) < 10:
            return []
        return build_candle_flow(ticks, period_seconds=300)

    PERIODIC_BACKFILL_INTERVAL_S = 600  # 10 minutes

    async def _periodic_gap_backfill_loop(self):
        """Check for candle gaps every 10 minutes during Globex hours.

        Catches mid-session gaps that the watchdog reconnect might miss
        (e.g. stream died briefly but reconnected without triggering backfill,
        or Databento historical wasn't available at reconnect time).
        """
        while self._running:
            await asyncio.sleep(self.PERIODIC_BACKFILL_INTERVAL_S)
            if not self._running:
                break
            now_epoch = time.time()
            if not self._in_globex(now_epoch):
                continue
            try:
                await self._backfill_gap()
            except Exception as e:
                logger.warning("Periodic gap backfill failed (non-fatal): %s", e)

    async def _backfill_gap(self):
        """Backfill candle gaps from Databento historical after reconnect.

        Finds the last candle in DB, computes the gap to now, and fetches
        missing 1m/5m bars from Databento historical API.
        """
        if not self._db_session_factory:
            return
        try:
            from ..repositories.market_repo import MarketRepo
            from ..market_data.databento_provider import DabentoProvider
            from ..config.trading_loader import get_market_data_config

            db_sym = self.symbol.split(".")[0]
            now = datetime.now(timezone.utc)
            fetch_end = now - timedelta(minutes=DATABENTO_HISTORICAL_DELAY_M)

            # Find last candle timestamp per interval
            session = self._db_session_factory()
            try:
                repo = MarketRepo(session)
                for interval in ("1m", "5m"):
                    latest = repo.get_latest_candle(db_sym, interval)
                    if not latest:
                        continue
                    latest_ts = latest.ts if latest.ts.tzinfo else latest.ts.replace(tzinfo=timezone.utc)
                    gap_seconds = (fetch_end - latest_ts).total_seconds()

                    if gap_seconds < MIN_GAP_FOR_BACKFILL_S:
                        continue

                    logger.info("Gap backfill %s: %s → %s (%.0f min gap)",
                                interval, latest_ts, fetch_end, gap_seconds / 60)

                    config = get_market_data_config()
                    inner = DabentoProvider(config)
                    db_symbol = config.get("symbol", "NQ.v.0")

                    bars = await asyncio.wait_for(
                        inner.get_bars(db_symbol, interval, latest_ts, fetch_end),
                        timeout=120.0,
                    )
                    if bars:
                        write_db = self._db_session_factory()
                        try:
                            write_repo = MarketRepo(write_db)
                            count = write_repo.bulk_insert_candles(db_sym, interval, bars)
                            logger.info("Gap backfill %s: inserted %d new bars", interval, count)
                        finally:
                            write_db.close()
            finally:
                session.close()
        except Exception as e:
            logger.warning("Gap backfill failed (non-fatal): %s", e)

    async def _stream_loop(self):
        # Backfill any gap before reconnecting to live
        await self._backfill_gap()

        try:
            import databento as db

            logger.info("Databento stream connecting to %s / %s ...", self.dataset, self.symbol)
            client = db.Live(key=self.api_key)
            # Subscribe to both Trades and MBP-1 (top of book)
            client.subscribe(
                dataset=self.dataset,
                schema="trades",
                symbols=[self.symbol],
                stype_in="continuous",
            )
            client.subscribe(
                dataset=self.dataset,
                schema="mbp-1",
                symbols=[self.symbol],
                stype_in="continuous",
            )
            logger.info("Databento stream subscribed, waiting for records...")

            record_count = 0
            async for record in client:
                if not self._running:
                    break

                ts = datetime.fromtimestamp(record.ts_event / 1e9, tz=timezone.utc)

                # Detect record type by schema
                # Check MBP-1 first: it also has side+size, so levels[] disambiguates
                if hasattr(record, "levels") and len(record.levels) >= 2:
                    # MBP-1 record — top of book
                    bid = record.levels[0]
                    ask = record.levels[1]
                    bid_px = bid.price / 1e9
                    ask_px = ask.price / 1e9
                    self.book.update(bid_px, bid.size, ask_px, ask.size, ts)

                    event = {
                        "type": "book",
                        "ts": ts.isoformat(),
                        "bid_price": bid_px,
                        "bid_size": bid.size,
                        "ask_price": ask_px,
                        "ask_size": ask.size,
                        "spread": self.book.spread,
                    }
                    self._publish(event)

                elif hasattr(record, "side") and hasattr(record, "size"):
                    # Trades record
                    price = record.price / 1e9
                    size = record.size
                    side = "A" if record.side == "A" else "B"

                    self.buffer.add(ts, price, size, side)

                    if self._tick_writer:
                        self._tick_writer.add(ts, price, size, side)

                    event = {
                        "type": "tick",
                        "ts": ts.isoformat(),
                        "price": price,
                        "size": size,
                        "side": side,
                        "cvd": self.buffer.cvd,
                        "delta_1m": self.buffer.delta_1m,
                    }
                    self._publish(event)

                    # Aggregate into running candles
                    ts_epoch = record.ts_event / 1e9

                    # 5m candle — persist on close (no UI emit)
                    _, closed_5m = self._candle_flow.update(price, record.size, ts_epoch)
                    if closed_5m and self._db_session_factory:
                        asyncio.create_task(self._write_closed_candle(closed_5m, "5m"))

                    # 1m candle — emit live snapshots + closed to UI, persist closed
                    emit_1m, closed_1m = self._candle_flow_1m.update(price, record.size, ts_epoch)
                    if emit_1m and self._in_globex(emit_1m["t"]):
                        self._publish(emit_1m)
                    if closed_1m:
                        if self._in_globex(closed_1m["t"]):
                            self._publish(closed_1m)
                        if self._db_session_factory:
                            asyncio.create_task(self._write_closed_candle(closed_1m, "1m"))

                    # Level proximity check
                    if self._level_monitor:
                        self._level_monitor.on_tick(price, record.size, ts_epoch)

                self._last_record_time = time.monotonic()
                record_count += 1
                if record_count in (1, 10, 100, 1000) or record_count % 10000 == 0:
                    logger.info("Databento stream: %d records received", record_count)

        except Exception as e:
            logger.error("Databento stream error: %s", e, exc_info=True)
            if self._running:
                logger.info("Databento stream reconnecting in 5s...")
                await asyncio.sleep(5)
                asyncio.create_task(self._stream_loop())

    def _publish(self, event: dict):
        """Publish event to all SSE subscribers.

        Thread-safe: if called from the stream thread, dispatches to the main
        event loop via call_soon_threadsafe. If called from the main loop, puts directly.
        """
        main_loop = getattr(self, '_main_loop', None)
        if main_loop and main_loop.is_running():
            # Called from stream thread — dispatch to main loop
            try:
                main_loop.call_soon_threadsafe(self._publish_direct, event)
            except RuntimeError:
                pass  # Loop closed
        else:
            self._publish_direct(event)

    def _publish_direct(self, event: dict):
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass
