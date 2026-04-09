"""PlayLoop — automated betting state machine.

Iterates a sorted queue of bets, handles provider tab management,
login waiting, navigation, and user-driven place/skip decisions.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .browser import MirrorBrowser
from .sse import MirrorBroadcaster
from .workflows import get_workflow

logger = logging.getLogger(__name__)

# State constants
STATE_IDLE = "idle"
STATE_RUNNING = "running"
STATE_PROVIDER_OPENING = "provider_opening"
STATE_LOGIN_WAITING = "login_waiting"
STATE_NAVIGATING = "navigating"
STATE_READY = "ready"
STATE_PLACING = "placing"

LOGIN_POLL_INTERVAL = 5.0   # seconds between login checks
LOGIN_TIMEOUT = 120.0       # seconds to wait for login before skipping provider


class PlayLoop:
    """Automated bet placement state machine.

    Usage:
        loop = PlayLoop(browser, broadcaster, proxy_url)
        loop.load_batch(bets, balances)
        loop.start()

        # From UI:
        loop.place()   # confirms current bet
        loop.skip()    # skips current bet
    """

    def __init__(self, browser: MirrorBrowser, broadcaster: MirrorBroadcaster, proxy_url: str):
        self._browser = browser
        self._broadcaster = broadcaster
        self._proxy_url = proxy_url.rstrip("/")

        # State
        self.state: str = STATE_IDLE
        self.current_bet: dict | None = None
        self.provider_stats: dict[str, dict] = {}

        # Queue
        self._queue: list[dict] = []
        self._queue_total: int = 0

        # Async control
        self._task: asyncio.Task | None = None
        self._place_event: asyncio.Event = asyncio.Event()
        self._skip_event: asyncio.Event = asyncio.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_batch(self, batch: list[dict], balances: dict[str, float]) -> None:
        """Filter to funded providers, sort by edge desc, build queue."""
        funded = {pid for pid, bal in balances.items() if bal > 0}
        filtered = [b for b in batch if b.get("provider_id") in funded]
        filtered.sort(key=lambda b: b.get("edge_pct", 0.0), reverse=True)
        self._queue = filtered
        self._queue_total = len(filtered)
        logger.info(
            f"[PlayLoop] Loaded {self._queue_total} bets "
            f"({len(batch) - self._queue_total} excluded — unfunded providers)"
        )

    def start(self) -> None:
        """Spawn the run loop as an asyncio task."""
        if self._task and not self._task.done():
            logger.warning("[PlayLoop] Already running — ignoring start()")
            return
        self._place_event.clear()
        self._skip_event.clear()
        self._task = asyncio.create_task(self._run(), name="play_loop")

    def stop(self) -> None:
        """Cancel the run loop and reset state."""
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        self.state = STATE_IDLE
        self.current_bet = None
        self._place_event.set()   # unblock any waits
        self._skip_event.set()

    def place(self) -> None:
        """Signal that the user wants to place the current bet."""
        self._skip_event.clear()
        self._place_event.set()

    def skip(self) -> None:
        """Signal that the user wants to skip the current bet."""
        self._place_event.clear()
        self._skip_event.set()

    def get_status(self) -> dict:
        return {
            "state": self.state,
            "current_bet": self.current_bet,
            "queue_remaining": len(self._queue),
            "queue_total": self._queue_total,
            "provider_stats": self.provider_stats,
        }

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        self.state = STATE_RUNNING
        logger.info("[PlayLoop] Starting run loop")

        current_provider: str | None = None

        try:
            while self._queue:
                bet = self._queue.pop(0)
                provider_id: str = bet.get("provider_id", "")

                # Init stats for this provider
                if provider_id not in self.provider_stats:
                    self.provider_stats[provider_id] = {"placed": 0, "skipped": 0, "total": 0}
                self.provider_stats[provider_id]["total"] += 1

                # Provider change — set up tab and wait for login
                if provider_id != current_provider:
                    if current_provider is not None:
                        self._broadcaster.publish("provider_complete", {"provider_id": current_provider})

                    current_provider = provider_id
                    workflow = get_workflow(provider_id)

                    # Find or open the provider tab
                    self.state = STATE_PROVIDER_OPENING
                    page = await workflow.find_tab(self._browser.context) if self._browser.context else None

                    if page is None:
                        domain = workflow.domain
                        url = f"https://{domain}" if domain else None
                        if url and self._browser.context:
                            logger.info(f"[PlayLoop] Opening tab for {provider_id}: {url}")
                            page = await self._browser.open_tab(url)
                        else:
                            logger.warning(f"[PlayLoop] No domain for {provider_id}, cannot open tab — skipping provider")
                            self._skip_provider(provider_id)
                            current_provider = None
                            continue

                    # Wait for login
                    self.state = STATE_LOGIN_WAITING
                    self._broadcaster.publish("login_waiting", {"provider_id": provider_id})
                    logged_in = await self._wait_for_login(workflow, page)

                    if not logged_in:
                        logger.warning(f"[PlayLoop] Login timeout for {provider_id} — skipping provider")
                        self._broadcaster.publish("provider_skipped", {
                            "provider_id": provider_id,
                            "reason": "login_timeout",
                        })
                        self._skip_provider(provider_id)
                        current_provider = None
                        continue

                # Navigate to event
                self.state = STATE_NAVIGATING
                self.current_bet = bet

                workflow = get_workflow(provider_id)
                page = await workflow.find_tab(self._browser.context) if self._browser.context else None
                if page is None:
                    logger.warning(f"[PlayLoop] Lost tab for {provider_id} mid-run — skipping bet")
                    self.provider_stats[provider_id]["skipped"] += 1
                    continue

                nav_ok = await workflow.navigate_to_event(page, bet)
                if not nav_ok:
                    logger.warning(f"[PlayLoop] Navigation failed for {provider_id} — skipping bet")
                    self._broadcaster.publish("bet_skipped", {"bet": bet, "reason": "navigation_failed"})
                    self.provider_stats[provider_id]["skipped"] += 1
                    continue

                # Ready — wait for user decision
                self.state = STATE_READY
                self._place_event.clear()
                self._skip_event.clear()
                self._broadcaster.publish("bet_ready", {"bet": bet, "provider_id": provider_id})

                done, _ = await asyncio.wait(
                    [
                        asyncio.ensure_future(self._place_event.wait()),
                        asyncio.ensure_future(self._skip_event.wait()),
                    ],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if self._place_event.is_set():
                    self.state = STATE_PLACING
                    stake = bet.get("stake", 0.0)
                    try:
                        result = await workflow.place_bet(page, bet, stake)
                        self._broadcaster.publish("bet_placed", {
                            "bet": bet,
                            "status": result.status,
                            "actual_odds": result.actual_odds,
                            "actual_stake": result.actual_stake,
                        })
                        self.provider_stats[provider_id]["placed"] += 1
                        await self._record_bet(bet, result)
                    except Exception:
                        logger.exception(f"[PlayLoop] place_bet() failed for {provider_id}")
                        self._broadcaster.publish("bet_error", {"bet": bet, "reason": "place_exception"})
                        self.provider_stats[provider_id]["skipped"] += 1
                else:
                    self._broadcaster.publish("bet_skipped", {"bet": bet, "reason": "user_skip"})
                    self.provider_stats[provider_id]["skipped"] += 1

            # Loop done
            if current_provider:
                self._broadcaster.publish("provider_complete", {"provider_id": current_provider})
            self._broadcaster.publish("play_complete", {"provider_stats": self.provider_stats})
            logger.info("[PlayLoop] Run complete")

        except asyncio.CancelledError:
            logger.info("[PlayLoop] Cancelled")
        except Exception:
            logger.exception("[PlayLoop] Unhandled error in run loop")
        finally:
            self.state = STATE_IDLE
            self.current_bet = None

    async def _wait_for_login(self, workflow, page) -> bool:
        """Poll check_login every LOGIN_POLL_INTERVAL up to LOGIN_TIMEOUT. Returns True if logged in."""
        elapsed = 0.0
        while elapsed < LOGIN_TIMEOUT:
            try:
                if await workflow.check_login(page):
                    return True
            except Exception:
                logger.debug(f"[PlayLoop] check_login() raised for {workflow.provider_id}", exc_info=True)
            await asyncio.sleep(LOGIN_POLL_INTERVAL)
            elapsed += LOGIN_POLL_INTERVAL
        return False

    def _skip_provider(self, provider_id: str) -> None:
        """Mark remaining bets for this provider as skipped and drain them from the queue."""
        remaining = [b for b in self._queue if b.get("provider_id") == provider_id]
        for b in remaining:
            self._queue.remove(b)
            self.provider_stats.setdefault(provider_id, {"placed": 0, "skipped": 0, "total": 0})
            self.provider_stats[provider_id]["skipped"] += 1
            self.provider_stats[provider_id]["total"] += 1

    async def _record_bet(self, bet: dict[str, Any], result) -> None:
        """POST placement result to the server DB."""
        url = f"{self._proxy_url}/api/opportunities/play/settle-bet"
        payload = {
            "bet": bet,
            "status": result.status,
            "actual_odds": result.actual_odds,
            "actual_stake": result.actual_stake,
            "reason": result.reason,
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    url,
                    json=payload,
                    headers={"X-Nginx-Authenticated": "firevsports"},
                )
                resp.raise_for_status()
                logger.info(f"[PlayLoop] Recorded bet {result.bet_id} — {result.status}")
        except Exception:
            logger.exception(f"[PlayLoop] Failed to record bet {result.bet_id}")
