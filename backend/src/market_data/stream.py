"""Databento live stream client for Trades + MBP-1."""
import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

logger = logging.getLogger(__name__)

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
    """Aggregates ticks into a running OHLCV candle for the current 5-min bucket."""

    BUCKET_SECONDS = 300  # 5 minutes
    EMIT_INTERVAL = 5.0   # seconds between candle event emissions

    def __init__(self):
        self._bucket_start: int = 0
        self._o = self._h = self._l = self._c = 0.0
        self._v = 0
        self._dirty = False
        self._last_emit: float = 0.0

    def _bucket_for(self, epoch: float) -> int:
        return int(epoch) // self.BUCKET_SECONDS * self.BUCKET_SECONDS

    def update(self, price: float, size: int, epoch: float) -> dict | None:
        """Feed a tick. Returns a candle event dict if it's time to emit, else None."""
        bucket = self._bucket_for(epoch)

        if bucket != self._bucket_start:
            # New bucket — reset
            self._bucket_start = bucket
            self._o = self._h = self._l = self._c = price
            self._v = size
        else:
            self._h = max(self._h, price)
            self._l = min(self._l, price)
            self._c = price
            self._v += size

        self._dirty = True

        now = time.monotonic()
        if now - self._last_emit >= self.EMIT_INTERVAL and self._dirty:
            self._last_emit = now
            self._dirty = False
            return self.snapshot()

        return None

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

    def __init__(
        self,
        api_key: str,
        dataset: str = "GLBX.MDP3",
        symbol: str = "NQ.FUT",
        db_session_factory: Callable | None = None,
    ):
        self.api_key = api_key
        self.dataset = dataset
        self.symbol = symbol
        self.buffer = TickBuffer()
        self.book = TopOfBook()
        self._candle_flow = CandleFlow()
        self._running = False
        self._task: asyncio.Task | None = None
        self._subscribers: list[asyncio.Queue] = []
        self._tick_writer: TickWriter | None = None
        if db_session_factory:
            self._tick_writer = TickWriter(db_session_factory, symbol=symbol.split(".")[0])

    async def start(self):
        if self._running:
            return
        self._running = True
        # Prune old ticks on startup
        if self._tick_writer:
            await self._tick_writer.start()
        self._task = asyncio.create_task(self._stream_loop())
        logger.info("Databento live stream started for %s", self.symbol)

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
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

    async def _stream_loop(self):
        try:
            import databento as db

            client = db.Live(key=self.api_key)
            # Subscribe to both Trades and MBP-1 (top of book)
            client.subscribe(
                dataset=self.dataset,
                schema="trades",
                symbols=[self.symbol],
            )
            client.subscribe(
                dataset=self.dataset,
                schema="mbp-1",
                symbols=[self.symbol],
            )

            async for record in client:
                if not self._running:
                    break

                ts = datetime.fromtimestamp(record.ts_event / 1e9, tz=timezone.utc)

                # Detect record type by schema
                if hasattr(record, "side") and hasattr(record, "size"):
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

                    # Aggregate into running candle and emit periodically
                    ts_epoch = record.ts_event / 1e9
                    candle_event = self._candle_flow.update(price, record.size, ts_epoch)
                    if candle_event:
                        self._publish(candle_event)

                elif hasattr(record, "levels") and len(record.levels) >= 2:
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

        except Exception as e:
            logger.error("Databento stream error: %s", e)
            if self._running:
                await asyncio.sleep(5)
                asyncio.create_task(self._stream_loop())

    def _publish(self, event: dict):
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass
