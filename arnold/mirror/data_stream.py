"""ProviderDataStream — continuous per-provider data polling for balance, positions, history."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from . import stream_registry
from .pending_loop import _detect_settlements

if TYPE_CHECKING:
    from playwright.async_api import Page

    from .sse import MirrorBroadcaster
    from .workflows.base import HistoryEntry, PositionEntry, ProviderWorkflow

logger = logging.getLogger(__name__)

# Default poll intervals (seconds)
BALANCE_INTERVAL = 30.0
POSITIONS_INTERVAL = 45.0
HISTORY_INTERVAL = 60.0

# If interceptor delivered fresh data within this window, skip the next active poll
INTERCEPT_FRESHNESS = 10.0

# History cache considered fresh for this long (used by play_loop)
HISTORY_CACHE_TTL = 90.0


class ProviderDataStream:
    """Continuous data stream for a single provider.

    Polls balance, positions, and history on staggered intervals.
    Also accepts passive updates from the browser interceptor.
    """

    def __init__(
        self,
        provider_id: str,
        workflow: ProviderWorkflow,
        page: Page,
        broadcaster: MirrorBroadcaster,
        proxy_url: str,
        *,
        balance_interval: float = BALANCE_INTERVAL,
        positions_interval: float = POSITIONS_INTERVAL,
        history_interval: float = HISTORY_INTERVAL,
    ):
        self.provider_id = provider_id
        self._workflow = workflow
        self._page = page
        self._broadcaster = broadcaster
        self._proxy_url = proxy_url.rstrip("/")

        # Poll intervals
        self._balance_iv = balance_interval
        self._positions_iv = positions_interval
        self._history_iv = history_interval

        # State cache
        self._balance: float | None = None
        self._positions: list[PositionEntry] = []
        self._history: list[HistoryEntry] = []
        self._history_ts: float = 0.0  # monotonic time of last history fetch
        self._placement_ids: dict[int, str] = {}  # our bet_id → provider_bet_id

        # Interceptor freshness tracking
        self._last_balance_intercept: float = 0.0

        # Task control
        self._task: asyncio.Task | None = None
        self._running = False
        self._started_at: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        self._started_at = time.monotonic()
        self._task = asyncio.create_task(self._run(), name=f"stream_{self.provider_id}")
        stream_registry.register(self.provider_id, self)
        logger.info(f"[DataStream] Started for {self.provider_id}")

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        stream_registry.unregister(self.provider_id)
        logger.info(f"[DataStream] Stopped for {self.provider_id}")

    @property
    def running(self) -> bool:
        return self._running

    # -- State readers (for play_loop / pending_loop) --

    def get_balance(self) -> float | None:
        return self._balance

    def get_positions(self) -> list[PositionEntry]:
        return list(self._positions)

    def get_history(self) -> list[HistoryEntry]:
        return list(self._history)

    def is_history_fresh(self) -> bool:
        return (time.monotonic() - self._history_ts) < HISTORY_CACHE_TTL

    def get_status(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "running": self._running,
            "balance": self._balance,
            "positions_count": len(self._positions),
            "history_count": len(self._history),
            "history_age_s": round(time.monotonic() - self._history_ts) if self._history_ts else None,
            "uptime_s": round(time.monotonic() - self._started_at) if self._started_at else 0,
        }

    # -- Passive hooks (called by browser interceptor via dispatch) --

    def on_balance_intercepted(self, balance: float) -> None:
        self._balance = balance
        self._last_balance_intercept = time.monotonic()
        self._broadcaster.publish(
            "stream_balance",
            {"provider_id": self.provider_id, "balance": balance, "source": "interceptor"},
        )

    def on_placement_intercepted(self, body: dict) -> None:
        pid = self._workflow.parse_placement_response(body)
        if pid:
            logger.info(f"[DataStream] {self.provider_id} placement intercepted: provider_bet_id={pid}")
            self._broadcaster.publish(
                "stream_placement",
                {"provider_id": self.provider_id, "provider_bet_id": pid},
            )

    def on_history_intercepted(self, url: str, body: str) -> None:
        # Interceptor caught a history response flowing by — trigger a fresh poll soon
        logger.debug(f"[DataStream] {self.provider_id} history intercepted from {url[:80]}")

    # ------------------------------------------------------------------
    # Internal poll loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        # Stagger initial polls so they don't all fire at once
        next_balance = time.monotonic() + 2.0
        next_positions = time.monotonic() + 5.0
        next_history = time.monotonic() + 8.0

        try:
            while self._running:
                # Safety: stop if page was closed
                if self._page.is_closed():
                    logger.info(f"[DataStream] {self.provider_id} page closed — stopping")
                    break

                now = time.monotonic()
                sleep_for = min(
                    max(next_balance - now, 0),
                    max(next_positions - now, 0),
                    max(next_history - now, 0),
                    5.0,  # max sleep so we check page.is_closed() often
                )
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)

                now = time.monotonic()

                if now >= next_balance:
                    await self._poll_balance()
                    next_balance = now + self._balance_iv

                if now >= next_positions:
                    await self._poll_positions()
                    next_positions = now + self._positions_iv

                if now >= next_history:
                    await self._poll_history()
                    next_history = now + self._history_iv

        except asyncio.CancelledError:
            logger.info(f"[DataStream] {self.provider_id} cancelled")
        except Exception:
            logger.exception(f"[DataStream] {self.provider_id} unhandled error")
        finally:
            self._running = False
            stream_registry.unregister(self.provider_id)

    async def _poll_balance(self) -> None:
        # Skip if interceptor delivered fresh data recently
        if (time.monotonic() - self._last_balance_intercept) < INTERCEPT_FRESHNESS:
            return
        try:
            balance = await self._workflow.sync_balance(self._page)
            if balance >= 0:
                self._balance = balance
                self._broadcaster.publish(
                    "stream_balance",
                    {"provider_id": self.provider_id, "balance": balance, "source": "api"},
                )
        except Exception:
            logger.warning(f"[DataStream] {self.provider_id} balance poll failed")

    async def _poll_positions(self) -> None:
        try:
            positions = await self._workflow.fetch_positions(self._page)
            self._positions = positions
            self._broadcaster.publish(
                "stream_positions",
                {
                    "provider_id": self.provider_id,
                    "positions": [
                        {
                            "provider_bet_id": p.provider_bet_id,
                            "event_name": p.event_name,
                            "market": p.market,
                            "outcome": p.outcome,
                            "odds": p.odds,
                            "stake": p.stake,
                            "placed_at": p.placed_at,
                            "potential_payout": p.potential_payout,
                        }
                        for p in positions
                    ],
                    "count": len(positions),
                },
            )
        except Exception:
            logger.warning(f"[DataStream] {self.provider_id} positions poll failed")

    async def _poll_history(self) -> None:
        try:
            history = await self._workflow.sync_history(self._page)
            self._history = history
            self._history_ts = time.monotonic()
            self._broadcaster.publish(
                "stream_history",
                {
                    "provider_id": self.provider_id,
                    "settled_count": len(history),
                },
            )
        except Exception:
            logger.warning(f"[DataStream] {self.provider_id} history poll failed")

    # ------------------------------------------------------------------
    # Settlement detection (used by play_loop / pending_loop)
    # ------------------------------------------------------------------

    def detect_settlements(self, db_pending: list[dict]) -> list[dict]:
        """Match DB pending bets against cached history using three-tier matching."""
        if not self._history:
            return []
        history_dicts = [
            {
                "odds": e.odds,
                "stake": e.stake,
                "status": e.status,
                "payout": e.payout,
                "provider_bet_id": e.provider_bet_id,
                "event_name": e.event_name,
            }
            for e in self._history
        ]
        return _detect_settlements(db_pending, history_dicts)
