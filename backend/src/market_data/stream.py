"""Databento live stream client for Trades + MBP-1 + Statistics."""
import asyncio
import logging
import threading
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


class StreamState:
    """Thread-safe shared state for SSE subscribers.

    The Databento stream thread writes latest snapshots here (tick, book, candle).
    SSE generators poll this state at a fixed interval — no call_soon_threadsafe
    needed, which prevents the main event loop from being overwhelmed.

    Dedup-safe events (tick, book, candle, orderflow, ml_features, dqn_inference)
    are stored as "latest" slots — only the most recent value matters.
    Must-not-lose events (level_touched, predictions, etc.) go into a ring buffer
    with sequence numbers so multiple subscribers can read independently.
    """

    # Event types where only the latest value matters
    SNAPSHOT_TYPES = frozenset({
        "tick", "book", "candle",
        "orderflow_update", "ml_features", "dqn_inference",
        "statistics",
    })

    def __init__(self):
        self._lock = threading.Lock()
        self._snapshots: dict[str, dict] = {}
        self._snapshot_versions: dict[str, int] = {}
        self._event_ring: deque[tuple[int, dict]] = deque(maxlen=500)
        self._event_seq: int = 0

    def set_latest(self, event_type: str, data: dict) -> None:
        """Update a dedup-safe snapshot slot (called from stream thread)."""
        with self._lock:
            self._snapshots[event_type] = data
            self._snapshot_versions[event_type] = self._snapshot_versions.get(event_type, 0) + 1

    def push_event(self, data: dict) -> None:
        """Queue a must-not-lose event (called from stream thread)."""
        with self._lock:
            self._event_seq += 1
            self._event_ring.append((self._event_seq, data))

    def poll(self, last_versions: dict[str, int], last_event_seq: int) -> tuple[list[dict], dict[str, int], int]:
        """Poll for new events since last call.

        Returns (events, new_versions, new_event_seq).
        Each SSE subscriber calls this independently with their own state.
        """
        events = []
        with self._lock:
            # Check snapshot slots for changes
            new_versions = dict(self._snapshot_versions)
            for etype, version in new_versions.items():
                if version != last_versions.get(etype, 0):
                    data = self._snapshots.get(etype)
                    if data:
                        events.append(data)

            # Collect queued events newer than last_event_seq
            for seq, data in self._event_ring:
                if seq > last_event_seq:
                    events.append(data)
            new_seq = self._event_seq

        return events, new_versions, new_seq


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

        def _write():
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

        await asyncio.to_thread(_write)

    @staticmethod
    async def prune_old_trades(db_session_factory: Callable, symbol: str = "NQ"):
        """Delete ticks older than current session (midnight CET/CEST)."""
        def _prune():
            from zoneinfo import ZoneInfo
            _CET = ZoneInfo("Europe/Stockholm")
            today_cet = datetime.now(timezone.utc).astimezone(_CET).date()
            cutoff = datetime(today_cet.year, today_cet.month, today_cet.day, tzinfo=_CET).astimezone(timezone.utc)
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

        await asyncio.to_thread(_prune)


class CandleFlow:
    """Aggregates ticks into a running OHLCV candle for a configurable time bucket."""

    # Reject ticks more than this fraction away from last known price (bad prints).
    # 0.2% ≈ 48 pts on NQ — catches contract roll artifacts (~75-400pt offset)
    # while allowing legitimate gaps (limit moves, halt reopens).
    MAX_TICK_DEVIATION = 0.002
    # After this many consecutive rejects, accept the tick (legitimate gap).
    # High threshold prevents old-contract trades from leaking through during roll.
    MAX_CONSECUTIVE_REJECTS = 100

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
    """Manages a persistent Databento live subscription (Trades + MBP-1 + Statistics)."""

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

    @staticmethod
    def _seconds_until_globex_open() -> float:
        """Seconds until the next Globex open (Sun 18:00 ET or daily 18:00 ET).

        Returns 0 if market is already open.
        """
        if DatabentoLiveStream._ET_TZ is None:
            from zoneinfo import ZoneInfo
            DatabentoLiveStream._ET_TZ = ZoneInfo("US/Eastern")
        now = datetime.now(DatabentoLiveStream._ET_TZ)
        wd = now.weekday()
        hour = now.hour

        if wd == 5:
            # Saturday — opens Sunday 18:00 ET
            days_ahead = 1
            target = now.replace(hour=18, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)
        elif wd == 6 and hour < 18:
            # Sunday before 18:00
            target = now.replace(hour=18, minute=0, second=0, microsecond=0)
        elif wd == 4 and hour >= 17:
            # Friday after 17:00 — opens Sunday 18:00 ET
            days_ahead = 2
            target = now.replace(hour=18, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)
        elif hour == 17:
            # Daily halt 17:00-18:00
            target = now.replace(hour=18, minute=0, second=0, microsecond=0)
        else:
            return 0  # market is open

        return max(0, (target - now).total_seconds())

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
        self._db_session_factory = db_session_factory
        self._candle_write_queue: deque[tuple[dict, str]] = deque(maxlen=500)
        self._candle_retry_task: asyncio.Task | None = None
        self._gap_backfill_task: asyncio.Task | None = None
        self.shared_state = StreamState()
        self._stream_thread_loop: asyncio.AbstractEventLoop | None = None
        self._stream_thread_ready = threading.Event()
        self._live_client = None  # Databento Live client — stored for explicit shutdown
        # Daily statistics from CME statistics schema
        self._daily_stats: dict[str, dict] = {}  # stat_name -> {value, ts}
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
        def _run_stream_thread():
            self._stream_thread_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._stream_thread_loop)
            self._stream_thread_ready.set()  # Signal that loop is available
            self._stream_thread_loop.run_until_complete(self._stream_loop())
        self._stream_thread = threading.Thread(target=_run_stream_thread, daemon=True, name="databento-stream")
        self._stream_thread.start()
        # Wait for stream thread loop in a non-blocking way
        await asyncio.to_thread(self._stream_thread_ready.wait, 10)

        # Watchdog and candle tasks stay on the main loop (lightweight, infrequent)
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        if self._db_session_factory:
            self._candle_retry_task = asyncio.create_task(self._candle_retry_loop())
            self._gap_backfill_task = asyncio.create_task(self._periodic_gap_backfill_loop())
        logger.info("Databento live stream started for %s (dedicated thread)", self.symbol)

    def _restart_stream_thread(self):
        """Restart the Databento stream in its dedicated thread."""
        self._running = False
        # Close client to unblock `async for record in client`
        if self._live_client:
            try:
                self._live_client.close()
            except Exception:
                pass
            self._live_client = None
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

        # Close Databento client first — unblocks `async for record in client`
        # in the stream thread so it can exit promptly
        if self._live_client:
            try:
                self._live_client.close()
            except Exception:
                pass
            self._live_client = None

        # Cancel main-loop tasks
        tasks_to_cancel = [self._task, self._watchdog_task, self._candle_retry_task, self._gap_backfill_task]
        for task in tasks_to_cancel:
            if task:
                task.cancel()
        self._task = self._watchdog_task = self._candle_retry_task = self._gap_backfill_task = None

        # Await cancelled tasks so they actually finish (prevents pending-task warnings)
        for task in tasks_to_cancel:
            if task:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        # Join the stream thread (with timeout — don't hang shutdown)
        if hasattr(self, '_stream_thread') and self._stream_thread.is_alive():
            await asyncio.to_thread(self._stream_thread.join, 3)

        if self._tick_writer:
            await self._tick_writer.stop()
        logger.info("Databento live stream stopped")

    def get_shared_state(self) -> StreamState:
        """Return the shared state for SSE polling."""
        return self.shared_state

    @property
    def daily_stats(self) -> dict[str, dict]:
        """All CME statistics received this session.

        Keys: open_interest, cleared_volume, block_volume, settlement_price,
              vwap, session_high, session_low, net_change.
        Values: {"value": int|float, "ts": datetime}
        """
        return dict(self._daily_stats)

    @property
    def open_interest(self) -> int | None:
        """Latest open interest from CME statistics feed (updated once daily)."""
        oi = self._daily_stats.get("open_interest")
        return oi["value"] if oi else None

    def set_level_monitor(self, monitor: LevelMonitor) -> None:
        """Attach a level monitor to receive tick callbacks."""
        self._level_monitor = monitor
        monitor.set_tick_buffer(self.buffer)
        monitor.set_candle_flow_source(self._get_recent_candles)

    async def _write_closed_candle(self, candle: dict, interval: str = "5m"):
        """Persist a completed candle bucket to market_candles DB table.

        Runs in a thread to avoid blocking the main event loop.
        On failure (e.g. DB locked), queues the candle for retry.
        """
        def _write():
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

        try:
            await asyncio.to_thread(_write)
        except Exception as e:
            self._candle_write_queue.append((candle, interval))
            logger.warning("Failed to persist closed %s candle (queued, %d pending): %s",
                           interval, len(self._candle_write_queue), e)

    async def _candle_retry_loop(self):
        """Periodically retry persisting queued candles that failed due to DB lock.

        DB writes run in a thread to avoid blocking the main event loop.
        """
        while self._running:
            await asyncio.sleep(30)
            if not self._candle_write_queue:
                continue
            batch = list(self._candle_write_queue)
            self._candle_write_queue.clear()

            def _write_batch():
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
                    for item in batch[written:]:
                        self._candle_write_queue.append(item)
                    logger.warning("Candle retry failed after %d/%d: %s", written, len(batch), e)

            await asyncio.to_thread(_write_batch)

    async def _watchdog_loop(self):
        """Monitor stream health — reconnect if no records received within timeout.

        Handles three transitions:
        1. Daily halt (17:00-18:00 ET): force reconnect at 18:00.
        2. Weekend close (Fri 17:00 → Sun 18:00): stop stream thread entirely,
           sleep until market opens, then restart — saves CPU, network, and
           avoids Databento "No data found" warnings.
        3. Mid-session stall: reconnect if no records within WATCHDOG_TIMEOUT_S.
        """
        was_in_halt = False
        _stream_suspended = False
        while self._running:
            await asyncio.sleep(self.WATCHDOG_TIMEOUT_S)
            if not self._running:
                break

            now_epoch = time.time()
            in_globex = self._in_globex(now_epoch)

            # --- Weekend / extended close: suspend stream and sleep ---
            if not in_globex and not _stream_suspended:
                from zoneinfo import ZoneInfo
                dt_et = datetime.fromtimestamp(now_epoch, tz=ZoneInfo("US/Eastern"))
                # Only suspend for weekend close (not daily halt — that's brief)
                is_weekend_close = (
                    dt_et.weekday() == 5  # Saturday
                    or (dt_et.weekday() == 4 and dt_et.hour >= 17)  # Friday after 17:00
                    or (dt_et.weekday() == 6 and dt_et.hour < 18)  # Sunday before 18:00
                )
                if is_weekend_close:
                    logger.info("Databento watchdog: weekend close detected — suspending stream thread")
                    # Stop stream thread to free network/CPU
                    self._running = False
                    if hasattr(self, '_stream_thread') and self._stream_thread.is_alive():
                        self._stream_thread.join(timeout=5)
                    _stream_suspended = True
                    self._running = True  # Keep watchdog alive

                    # Sleep until market opens (check every 60s for cancellation)
                    sleep_s = self._seconds_until_globex_open()
                    logger.info("Databento watchdog: sleeping %.0f min until Globex opens", sleep_s / 60)
                    slept = 0.0
                    while slept < sleep_s and self._running:
                        await asyncio.sleep(min(60, sleep_s - slept))
                        slept += 60
                    if not self._running:
                        break

                    # Market is open — restart stream
                    logger.info("Databento watchdog: Globex open — restarting stream thread")
                    self._last_record_time = time.monotonic()
                    self._restart_stream_thread()
                    _stream_suspended = False
                    continue

            # --- Daily halt → open transition: force reconnect ---
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

            # --- Mid-session stall detection ---
            elapsed = time.monotonic() - self._last_record_time
            if elapsed > self.WATCHDOG_TIMEOUT_S:
                if in_globex and not _stream_suspended:
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

        Runs backfill in a background thread to avoid starving the main event loop
        (Databento historical API calls can take 30-120s).
        """
        import threading
        while self._running:
            await asyncio.sleep(self.PERIODIC_BACKFILL_INTERVAL_S)
            if not self._running:
                break
            now_epoch = time.time()
            if not self._in_globex(now_epoch):
                continue

            def _run():
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(self._backfill_gap())
                except Exception as e:
                    logger.warning("Periodic gap backfill failed (non-fatal): %s", e)
                finally:
                    loop.close()
            threading.Thread(target=_run, daemon=True, name="periodic-backfill").start()

    async def _backfill_gap(self):
        """Backfill candle gaps from Databento historical after reconnect.

        Scans existing candles for mid-series gaps (not just tail gaps),
        then fetches missing 1m/5m bars from Databento historical API.
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
            # Look back 24h for gaps
            lookback_start = now - timedelta(hours=24)

            config = get_market_data_config()
            db_symbol = config.get("symbol", "NQ.v.0")

            session = self._db_session_factory()
            try:
                repo = MarketRepo(session)
                for interval in ("1m", "5m"):
                    rows = repo.get_candles(db_sym, interval, lookback_start, now)
                    if len(rows) < 2:
                        # Tail-gap fallback: no rows means backfill from oldest/latest
                        latest = repo.get_latest_candle(db_sym, interval)
                        if not latest:
                            continue
                        latest_ts = latest.ts if latest.ts.tzinfo else latest.ts.replace(tzinfo=timezone.utc)
                        gap_seconds = (fetch_end - latest_ts).total_seconds()
                        if gap_seconds < MIN_GAP_FOR_BACKFILL_S:
                            continue
                        gaps = [(latest_ts, fetch_end)]
                    else:
                        # Scan for mid-series gaps
                        bucket_s = 60 if interval == "1m" else 300
                        max_gap = bucket_s * 3  # 2 missing bars is OK
                        gaps = []
                        for i in range(1, len(rows)):
                            ts_prev = rows[i - 1].ts if rows[i - 1].ts.tzinfo else rows[i - 1].ts.replace(tzinfo=timezone.utc)
                            ts_curr = rows[i].ts if rows[i].ts.tzinfo else rows[i].ts.replace(tzinfo=timezone.utc)
                            diff = (ts_curr - ts_prev).total_seconds()
                            if diff > max_gap:
                                # Only backfill gaps old enough for Databento historical
                                if (now - ts_curr).total_seconds() > DATABENTO_HISTORICAL_DELAY_M * 60:
                                    gaps.append((ts_prev, ts_curr))

                    if not gaps:
                        continue

                    inner = DabentoProvider(config)
                    for gap_start, gap_end in gaps:
                        logger.info("Gap backfill %s: %s → %s (%.0f min gap)",
                                    interval, gap_start, gap_end,
                                    (gap_end - gap_start).total_seconds() / 60)

                        bars = await asyncio.wait_for(
                            inner.get_bars(db_symbol, interval, gap_start, gap_end),
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
        # If market is closed, sleep until it opens instead of connecting
        # (avoids "No data found" warnings and wasted Databento API calls)
        if not self._in_globex(time.time()):
            next_open = self._seconds_until_globex_open()
            if next_open > 0:
                logger.info(
                    "Databento stream: market closed — sleeping %.0f min until Globex opens",
                    next_open / 60,
                )
                # Sleep in 60s chunks so we can be cancelled cleanly
                slept = 0.0
                while slept < next_open and self._running:
                    await asyncio.sleep(min(60, next_open - slept))
                    slept += 60
                if not self._running:
                    return
                logger.info("Databento stream: market open — connecting now")

        # Backfill any gap before reconnecting to live
        await self._backfill_gap()

        try:
            import databento as db

            logger.info("Databento stream connecting to %s / %s ...", self.dataset, self.symbol)
            client = db.Live(key=self.api_key)
            self._live_client = client  # Store for explicit shutdown
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
            client.subscribe(
                dataset=self.dataset,
                schema="statistics",
                symbols=[self.symbol],
                stype_in="continuous",
            )
            logger.info("Databento stream subscribed (trades + mbp-1 + statistics), waiting for records...")

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

                elif hasattr(record, "stat_type"):
                    # Statistics record — capture all useful CME daily/intraday stats
                    from databento_dbn import StatType
                    _QUANTITY_STATS = {
                        StatType.OPEN_INTEREST: "open_interest",
                        StatType.CLEARED_VOLUME: "cleared_volume",
                        StatType.BLOCK_VOLUME: "block_volume",
                    }
                    _PRICE_STATS = {
                        StatType.SETTLEMENT_PRICE: "settlement_price",
                        StatType.VWAP: "vwap",
                        StatType.TRADING_SESSION_HIGH_PRICE: "session_high",
                        StatType.TRADING_SESSION_LOW_PRICE: "session_low",
                        StatType.NET_CHANGE: "net_change",
                    }
                    st = record.stat_type
                    if st in _QUANTITY_STATS:
                        name = _QUANTITY_STATS[st]
                        self._daily_stats[name] = {"value": record.quantity, "ts": ts}
                        logger.info("Stat %s: %s @ %s", name, f"{record.quantity:,}", ts.isoformat())
                    elif st in _PRICE_STATS:
                        name = _PRICE_STATS[st]
                        value = record.price / 1e9
                        self._daily_stats[name] = {"value": value, "ts": ts}
                        logger.info("Stat %s: %.2f @ %s", name, value, ts.isoformat())
                    else:
                        name = None

                    if name:
                        self._publish({
                            "type": "statistics",
                            "ts": ts.isoformat(),
                            "stat": name,
                            **{k: v["value"] for k, v in self._daily_stats.items()},
                        })

                self._last_record_time = time.monotonic()
                record_count += 1
                if record_count in (1, 10, 100, 1000) or record_count % 10000 == 0:
                    logger.info("Databento stream: %d records received", record_count)

        except Exception as e:
            if self._running:
                logger.error("Databento stream error: %s", e, exc_info=True)
                logger.info("Databento stream reconnecting in 5s...")
                self._live_client = None
                await asyncio.sleep(5)
                asyncio.create_task(self._stream_loop())
            else:
                logger.info("Databento stream closed (shutdown)")
                self._live_client = None

    def _publish(self, event: dict):
        """Publish event to shared state (thread-safe, no main loop interaction).

        SSE subscribers poll the shared state independently — this method
        never touches the main event loop, preventing it from being overwhelmed
        by high-frequency Databento tick data.
        """
        etype = event.get("type", "")
        if etype in StreamState.SNAPSHOT_TYPES:
            self.shared_state.set_latest(etype, event)
        else:
            self.shared_state.push_event(event)
