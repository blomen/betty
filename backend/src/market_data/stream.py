"""Databento live stream client for Trades + MBP-1."""
import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class TickBuffer:
    """Thread-safe circular buffer of recent ticks."""
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


class DatabentoLiveStream:
    """Manages a persistent Databento live subscription."""

    def __init__(self, api_key: str, dataset: str = "GLBX.MDP3", symbol: str = "NQ.FUT"):
        self.api_key = api_key
        self.dataset = dataset
        self.symbol = symbol
        self.buffer = TickBuffer()
        self._running = False
        self._task: asyncio.Task | None = None
        self._subscribers: list[asyncio.Queue] = []

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._stream_loop())
        logger.info("Databento live stream started for %s", self.symbol)

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
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
            client.subscribe(
                dataset=self.dataset,
                schema="trades",
                symbols=[self.symbol],
            )

            async for record in client:
                if not self._running:
                    break

                ts = datetime.fromtimestamp(record.ts_event / 1e9, tz=timezone.utc)
                price = record.price / 1e9
                size = record.size
                side = "A" if record.side == "A" else "B"

                self.buffer.add(ts, price, size, side)

                event = {
                    "ts": ts.isoformat(),
                    "price": price,
                    "size": size,
                    "side": side,
                    "cvd": self.buffer.cvd,
                    "delta_1m": self.buffer.delta_1m,
                }

                for q in self._subscribers:
                    try:
                        q.put_nowait(event)
                    except asyncio.QueueFull:
                        pass

        except Exception as e:
            logger.error("Databento stream error: %s", e)
            if self._running:
                await asyncio.sleep(5)
                asyncio.create_task(self._stream_loop())
