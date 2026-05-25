"""SlipOddsStream — poll a single loaded slip widget for live odds.

One stream per provider tab where a slip is loaded. Polls
`workflow.read_slip_odds(page)` at a configurable interval and invokes
`on_odds_change(odds)` whenever the value changes (suppresses no-ops).

ArbRunner aggregates across legs by instantiating one stream per leg and
combining their `current_odds` on each tick.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from playwright.async_api import Page

    from .workflows.base import ProviderWorkflow

logger = logging.getLogger(__name__)

# Module-level shared client for the optional log-endpoint POST path.
# Lazy-initialized — stays unused unless a stream is constructed with a
# log_endpoint, which neither production caller does today. This avoids
# a fresh TCP+TLS handshake per tick if the path is ever turned on.
_log_client: httpx.AsyncClient | None = None


def _get_log_client() -> httpx.AsyncClient:
    global _log_client
    if _log_client is None or _log_client.is_closed:
        _log_client = httpx.AsyncClient(
            timeout=2.0,
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
        )
    return _log_client


class SlipOddsStream:
    def __init__(
        self,
        provider_id: str,
        workflow: ProviderWorkflow,
        page: Page,
        on_odds_change: Callable[[float], None],
        poll_interval_s: float = 1.0,
        log_endpoint: str | None = None,
        bet_context: dict | None = None,
    ):
        self.provider_id = provider_id
        self._workflow = workflow
        self._page = page
        self._on_odds_change = on_odds_change
        self._poll_interval_s = poll_interval_s
        self._task: asyncio.Task | None = None
        self._current_odds: float | None = None
        self._log_endpoint = log_endpoint
        self._bet_context = bet_context

    @property
    def page(self) -> Page:
        return self._page

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def current_odds(self) -> float | None:
        return self._current_odds

    def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.create_task(self._loop(), name=f"slip_odds_{self.provider_id}")

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _loop(self) -> None:
        try:
            while True:
                try:
                    odds = await self._workflow.read_slip_odds(self._page)
                except Exception:
                    logger.debug(f"[SlipStream:{self.provider_id}] read_slip_odds raised", exc_info=True)
                    odds = None

                if odds is not None and odds != self._current_odds:
                    self._current_odds = odds
                    try:
                        self._on_odds_change(odds)
                    except Exception:
                        logger.exception(f"[SlipStream:{self.provider_id}] callback raised")
                    if self._log_endpoint and self._bet_context:
                        asyncio.create_task(
                            self._post_tick(odds),
                            name=f"slip_odds_log_{self.provider_id}",
                        )

                await asyncio.sleep(self._poll_interval_s)
        except asyncio.CancelledError:
            pass

    async def _post_tick(self, odds: float) -> None:
        try:
            client = _get_log_client()
            await client.post(
                self._log_endpoint,
                json={
                    "provider_id": self.provider_id,
                    "event_id": self._bet_context.get("event_id", ""),
                    "market": self._bet_context.get("market", ""),
                    "outcome": self._bet_context.get("outcome", ""),
                    "scraped_odds": odds,
                    "scanner_odds": self._bet_context.get("scanner_odds"),
                },
            )
        except Exception:
            logger.debug(f"[SlipStream:{self.provider_id}] log post failed", exc_info=True)
