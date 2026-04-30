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

from .play_loop import STATE_RUNNING
from .provider_runner import ProviderRunner
from .workflows import get_workflow

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
                wf = get_workflow(self.provider_id)
                self._lent_page = await wf.find_tab(self._browser.context)
            return self._lent_page
        if self._lent_to_group_id is not None:
            logger.warning(
                f"[PinnacleShared] lend_to_arb({arb_group_id}) called while already lent to "
                f"{self._lent_to_group_id} — returning shared page anyway"
            )
            return self._lent_page
        wf = get_workflow(self.provider_id)
        page = await wf.find_tab(self._browser.context)
        if page is None:
            # Tab not found — don't lend; the lent state is reserved for "I have a page".
            logger.warning(f"[PinnacleShared] lend_to_arb({arb_group_id}) — no tab found; not lending")
            return None
        self._pre_lend_state = self.state
        self.state = STATE_LENT_TO_ARB
        self._lent_to_group_id = arb_group_id
        self._lent_page = page
        self._lent_event.clear()
        self._broadcaster.publish("pinnacle_lent", {"arb_group_id": arb_group_id})
        return page

    def release_to_value(self) -> None:
        """Release the lent Pinnacle tab back to the value-bet loop.

        Contract: the caller (ArbRunner) MUST stop using the Page reference
        returned by lend_to_arb() before invoking this. The value loop unblocks
        immediately on release; if the caller still holds a Page reference and
        issues another Playwright call after release, it races against the
        value loop's navigation.

        No-op when the runner is not currently lent.
        """
        if self._lent_to_group_id is None:
            return
        group_id = self._lent_to_group_id
        self._lent_to_group_id = None
        # Restore prior state when known, else fall back to RUNNING — the value
        # loop re-derives state on each iteration so this is just the resume hint.
        self.state = self._pre_lend_state or STATE_RUNNING
        self._pre_lend_state = None
        self._lent_page = None
        self._lent_event.set()
        self._broadcaster.publish("pinnacle_released", {"arb_group_id": group_id})

    async def _await_unlent_or_done(self) -> None:
        """Public hook that blocks until the runner is free.

        Production blocking happens via the popper-gate in `_run` — that's
        the only mechanism that actually pauses the value loop today. This
        method exists so external callers and tests can wait on the same
        condition without poking at `_lent_event` directly.
        """
        await self._lent_event.wait()

    async def _run(self) -> None:
        """Wrap ProviderRunner._run so we yield via _pop_bet while lent.

        Replace the bound _pop_bet with a gated shim: when lent, the popper
        returns None — which sends the parent loop into its queue-empty idle
        path (5s sleep then re-poll). When released, the popper delegates to
        the original. This avoids copying the parent's loop body.
        """
        original_pop = self._pop_bet

        def _gated_pop():
            if not self._lent_event.is_set():
                return None
            return original_pop()

        self._pop_bet = _gated_pop  # type: ignore
        try:
            await super()._run()
        finally:
            self._pop_bet = original_pop  # type: ignore
