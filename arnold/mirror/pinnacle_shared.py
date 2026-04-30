"""PinnacleSharedRunner — value-bet runner that can lend its tab to ArbRunner.

When the user selects both a soft anchor (e.g. betinia) and pinnacle, two
runners would otherwise share the Pinnacle tab and overwrite each other's
slip. This class arbitrates: in `value` mode it behaves like a ProviderRunner
playing value bets; on `lend_to_arb()` it stops navigating, returns the page
to ArbRunner, and waits for `release_to_value()` before resuming.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from .provider_runner import ProviderRunner

if TYPE_CHECKING:
    from playwright.async_api import Page

    from .browser import MirrorBrowser
    from .sse import MirrorBroadcaster

logger = logging.getLogger(__name__)

STATE_LENT_TO_ARB = "lent_to_arb"


class PinnacleSharedRunner(ProviderRunner):
    """ProviderRunner subclass that supports lending its Pinnacle tab to ArbRunner.

    Public additions:
      lend_to_arb(arb_group_id) -> Page  (blocks until tab is found)
      release_to_value()                 (no-op if not lent)

    Internally we hold an asyncio.Event named `_lent_event`. It is set when
    the runner is free, cleared when an arb has borrowed the tab. The value
    loop must `await self._lent_event.wait()` before each navigation step.
    """

    def __init__(
        self,
        provider_id: str,
        browser: MirrorBrowser,
        broadcaster: MirrorBroadcaster,
        proxy_url: str,
        pop_bet: Callable[[], dict | None],
        block_event_market: Callable[[dict], None],
        is_blocked: Callable[[dict], bool],
        placed_today: dict[str, int],
        peek_top_edge: Callable[[], float | None] | None = None,
        stake_caps: dict[str, float] | None = None,
        mark_recently_skipped: Callable[[dict], None] | None = None,
    ):
        super().__init__(
            provider_id=provider_id,
            browser=browser,
            broadcaster=broadcaster,
            proxy_url=proxy_url,
            pop_bet=pop_bet,
            block_event_market=block_event_market,
            is_blocked=is_blocked,
            placed_today=placed_today,
            peek_top_edge=peek_top_edge,
            stake_caps=stake_caps,
            mark_recently_skipped=mark_recently_skipped,
        )
        self._lent_event: asyncio.Event = asyncio.Event()
        self._lent_event.set()  # start in "free" state
        self._lent_to_group_id: str | None = None
        self._pre_lend_state: str | None = None
        self._lent_page: Page | None = None

    async def _find_tab(self, context):
        """Indirection so tests can stub tab discovery without touching Playwright."""
        from .workflows import get_workflow

        wf = get_workflow(self.provider_id)
        return await wf.find_tab(context)

    async def lend_to_arb(self, arb_group_id: str) -> Page | None:
        """Mark the runner as lent, return the current Pinnacle page.

        Idempotent: a second call with the same arb_group_id returns the same
        page without re-emitting `pinnacle_lent`. A different group_id while
        already lent logs a warning and returns the current page anyway —
        ArbRunner is responsible for not overlapping arbs on the same tab.
        """
        if self._lent_to_group_id == arb_group_id:
            # Idempotent: return the cached page without re-emitting
            if self._lent_page is None:
                self._lent_page = await self._find_tab(self._browser.context)
            return self._lent_page
        if self._lent_to_group_id is not None:
            logger.warning(
                f"[PinnacleShared] lend_to_arb({arb_group_id}) called while already lent to "
                f"{self._lent_to_group_id} — returning shared page anyway"
            )
            return self._lent_page
        self._pre_lend_state = self.state
        self.state = STATE_LENT_TO_ARB
        self._lent_to_group_id = arb_group_id
        self._lent_event.clear()
        self._broadcaster.publish("pinnacle_lent", {"arb_group_id": arb_group_id})
        page = await self._find_tab(self._browser.context)
        self._lent_page = page
        return page

    def release_to_value(self) -> None:
        """Mark the runner as free again. No-op if not lent."""
        if self._lent_to_group_id is None:
            return
        group_id = self._lent_to_group_id
        self._lent_to_group_id = None
        # Don't restore the previous state literally — the value loop will
        # re-derive its state on the next iteration. Just leave a known-good
        # idle marker.
        from .play_loop import STATE_RUNNING

        self.state = self._pre_lend_state or STATE_RUNNING
        self._pre_lend_state = None
        self._lent_page = None
        self._lent_event.set()
        self._broadcaster.publish("pinnacle_released", {"arb_group_id": group_id})
