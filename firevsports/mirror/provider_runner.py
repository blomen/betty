"""ProviderRunner — independent per-provider play loop task.

Each runner owns its own state machine and processes bets from a shared
cluster queue. Multiple runners can run in parallel across different providers.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import httpx

from .pending_loop import _detect_settlements
from .workflows import get_workflow
from .workflows.base import PlacementResult

if TYPE_CHECKING:
    from .browser import MirrorBrowser
    from .sse import MirrorBroadcaster

logger = logging.getLogger(__name__)

# Re-import constants from play_loop to stay in sync
from .play_loop import (
    _AUTH_HEADER,
    _AUTH_VALUE,
    _CLUSTER_MEMBERS,
    _PROVIDER_TO_CLUSTER,
    DAILY_BET_CAP,
    LOGIN_POLL_INTERVAL,
    LOGIN_TIMEOUT,
    STATE_IDLE,
    STATE_LOGIN_WAITING,
    STATE_NAVIGATING,
    STATE_PLACING,
    STATE_PROVIDER_OPENING,
    STATE_READY,
    STATE_SETTLING,
    UNCAPPED_PROVIDERS,
    _bet_ns,
)


class ProviderRunner:
    """Runs the play loop for a single provider as an asyncio task."""

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
    ):
        self.provider_id = provider_id
        self._browser = browser
        self._broadcaster = broadcaster
        self._proxy_url = proxy_url.rstrip("/")
        self._pop_bet = pop_bet
        self._block_event_market = block_event_market
        self._is_blocked = is_blocked
        self._placed_today = placed_today

        # Per-runner state
        self.state: str = STATE_IDLE
        self.current_bet: dict | None = None
        self.stats: dict = {"placed": 0, "skipped": 0, "total": 0}

        # Async events — per-runner, not shared
        self._bet_intercepted_event = asyncio.Event()
        self._skip_event = asyncio.Event()
        self._intercepted_body: dict | None = None
        self._intercepted_request_body: dict | None = None

        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name=f"runner_{self.provider_id}")

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        self.state = STATE_IDLE
        self.current_bet = None
        self._bet_intercepted_event.set()
        self._skip_event.set()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def skip(self) -> None:
        self._skip_event.set()

    def on_bet_intercepted(self, body: dict, request_body: dict | None = None) -> None:
        if self.state in (STATE_READY, STATE_NAVIGATING, STATE_PLACING):
            logger.info(f"[Runner:{self.provider_id}] Bet intercepted (state={self.state})")
            self._intercepted_body = body
            self._intercepted_request_body = request_body
            self._bet_intercepted_event.set()
        else:
            # Runner busy (settling/login/idle) — record asynchronously so the bet isn't lost
            logger.warning(f"[Runner:{self.provider_id}] Bet intercepted in state={self.state} — recording async")
            asyncio.create_task(
                self._record_async_interception(body, request_body),
                name=f"async_bet_{self.provider_id}",
            )

    async def _record_async_interception(self, body: dict, request_body: dict | None) -> None:
        """Record a bet intercepted while the runner wasn't in READY state."""
        from .workflows import get_workflow

        pid = self.provider_id
        workflow = get_workflow(pid)

        # Validate placement succeeded
        if hasattr(workflow, "parse_placement_status"):
            try:
                pstatus = workflow.parse_placement_status(body)
                if not pstatus["success"]:
                    logger.info(f"[Runner:{pid}] Async interception was a failed placement — ignoring")
                    return
            except Exception:
                pass

        # Extract details from response
        provider_bet_id = None
        actual_odds = None
        actual_stake = None
        try:
            provider_bet_id = workflow.parse_placement_response(body)
        except Exception:
            pass
        if hasattr(workflow, "parse_placement_details"):
            try:
                details = workflow.parse_placement_details(body)
                actual_odds = details.get("actual_odds")
                actual_stake = details.get("actual_stake")
            except Exception:
                pass
        if not actual_stake and request_body and hasattr(workflow, "parse_placement_request_stake"):
            try:
                actual_stake = workflow.parse_placement_request_stake(request_body)
            except Exception:
                pass

        # Try to match against current bet context
        bet = self.current_bet
        if bet:
            result = PlacementResult(
                status="placed",
                bet_id=provider_bet_id or 0,
                actual_odds=actual_odds or bet.get("odds", 0),
                actual_stake=actual_stake or bet.get("stake", 0),
                reason="async_interception",
            )
            await self._record_bet(bet, result)
            self._block_event_market(bet)
            self._placed_today[pid] = self._placed_today.get(pid, 0) + 1
            self._broadcaster.publish(
                "bet_placed",
                {
                    "bet": bet,
                    "status": "placed",
                    "actual_odds": result.actual_odds,
                    "actual_stake": result.actual_stake,
                    "placed_today": self._placed_today.get(pid, 0),
                    "daily_cap": DAILY_BET_CAP,
                },
            )
        else:
            logger.warning(
                f"[Runner:{pid}] Async interception but no current bet context — "
                f"bet_id={provider_bet_id} odds={actual_odds} stake={actual_stake}. "
                f"Will be picked up by settlement sync."
            )

    def get_status(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "state": self.state,
            "current_bet": self.current_bet,
            "stats": self.stats,
            "placed_today": self._placed_today.get(self.provider_id, 0),
        }

    # ------------------------------------------------------------------
    # Main loop — extracted from PlayLoop._run()
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        self.state = STATE_PROVIDER_OPENING
        pid = self.provider_id
        logger.info(f"[Runner:{pid}] Starting")

        try:
            workflow = get_workflow(pid)

            # 1. Find tab
            self._broadcaster.publish("provider_opening", {"provider_id": pid})
            page = None
            for _attempt in range(10):
                if self._browser.context:
                    page = await workflow.find_tab(self._browser.context)
                    if page is None:
                        for p in self._browser.context.pages:
                            if workflow.domain and workflow.domain in p.url:
                                page = p
                                break
                if page:
                    break
                await asyncio.sleep(1)

            if page is None:
                logger.warning(f"[Runner:{pid}] No tab found — stopping")
                self._broadcaster.publish("provider_skipped", {"provider_id": pid, "reason": "no_tab"})
                return

            # 2. Wait for login
            self.state = STATE_LOGIN_WAITING
            self._broadcaster.publish("login_waiting", {"provider_id": pid})
            logged_in = await self._wait_for_login(workflow, page)
            if not logged_in:
                logger.warning(f"[Runner:{pid}] Login timeout — stopping")
                self._broadcaster.publish("provider_skipped", {"provider_id": pid, "reason": "login_timeout"})
                return

            # 3. Detect settlements (broadcast only — user confirms from UI)
            await self._detect_pending(pid, workflow, page)

            # 4. Check daily cap
            if pid not in UNCAPPED_PROVIDERS:
                await self._fetch_placed_today(pid)
                placed = self._placed_today.get(pid, 0)
                if placed >= DAILY_BET_CAP:
                    logger.info(f"[Runner:{pid}] At daily cap ({placed}/{DAILY_BET_CAP})")
                    self._broadcaster.publish(
                        "provider_complete",
                        {"provider_id": pid, "reason": f"daily cap ({placed}/{DAILY_BET_CAP})"},
                    )
                    return

            # 5. Process bets from shared queue
            logger.info(f"[Runner:{pid}] Entering bet loop")
            while True:
                if pid not in UNCAPPED_PROVIDERS:
                    placed = self._placed_today.get(pid, 0)
                    if placed >= DAILY_BET_CAP:
                        self._broadcaster.publish(
                            "provider_complete",
                            {"provider_id": pid, "reason": f"daily cap ({placed}/{DAILY_BET_CAP})"},
                        )
                        break

                bet = self._pop_bet()
                if bet is None:
                    logger.info(f"[Runner:{pid}] Queue empty — done")
                    break

                if self._is_blocked(bet):
                    logger.debug(f"[Runner:{pid}] Skipping blocked bet: {bet.get('event_id')} {bet.get('market')}")
                    continue

                # Skip events where provider already has an open position
                meta = bet.get("provider_meta") or {}
                provider_eid = str(meta.get("event_id", ""))
                if provider_eid and hasattr(workflow, "_open_kambi_eids") and provider_eid in workflow._open_kambi_eids:
                    logger.info(
                        f"[Runner:{pid}] Skipping — already have open bet on event {provider_eid} "
                        f"({bet.get('display_home')} v {bet.get('display_away')})"
                    )
                    self._broadcaster.publish(
                        "bet_skipped",
                        {"bet": bet, "reason": f"existing open position on event {provider_eid}"},
                    )
                    self.stats["skipped"] += 1
                    continue

                self.stats["total"] += 1
                bet["provider_id"] = pid

                # Navigate
                self.state = STATE_NAVIGATING
                self.current_bet = bet
                bet_ns = _bet_ns(bet)
                logger.info(
                    f"[Runner:{pid}] Next bet: {bet.get('display_home')} v {bet.get('display_away')} "
                    f"{bet.get('outcome')} @ {bet.get('odds')} | "
                    f"gecko_eid={getattr(bet_ns, 'gecko_event_id', '')} "
                    f"meta={bet.get('provider_meta')}"
                )

                workflow = get_workflow(pid)
                page = await workflow.find_tab(self._browser.context) if self._browser.context else None
                if page is None:
                    logger.warning(f"[Runner:{pid}] Lost tab mid-run — skipping bet")
                    self.stats["skipped"] += 1
                    continue

                bet_ns = _bet_ns(bet)
                nav_ok = await workflow.navigate_to_event(page, bet_ns)
                if not nav_ok:
                    logger.warning(f"[Runner:{pid}] Navigation failed — skipping bet")
                    self._broadcaster.publish("bet_skipped", {"bet": bet, "reason": "navigation_failed"})
                    self.stats["skipped"] += 1
                    continue

                if await self._is_event_closed(page):
                    self._broadcaster.publish("bet_skipped", {"bet": bet, "reason": "event_closed"})
                    self.stats["skipped"] += 1
                    continue

                # Prep betslip
                stake = bet.get("stake", 0.0)
                cached_bal = self._browser.provider_data.get(pid, {}).get("balance")
                if cached_bal is not None and cached_bal > 0 and stake > cached_bal:
                    stake = cached_bal
                    bet["stake"] = stake
                    bet_ns.stake = stake
                prep_result = await workflow.prep_betslip(page, bet_ns, stake)

                # Check live price
                live_odds = prep_result.actual_odds
                live_edge = bet.get("edge_pct")
                if hasattr(workflow, "check_live_price"):
                    try:
                        lo, le = await workflow.check_live_price(page, bet_ns)
                        if lo is not None:
                            live_odds = lo
                            live_edge = le
                    except Exception:
                        pass

                # Auto-skip negative EV
                if live_edge is not None and live_edge < 0:
                    logger.info(f"[Runner:{pid}] Auto-skip: live edge {live_edge:.1f}%")
                    self._broadcaster.publish(
                        "bet_skipped",
                        {
                            "bet": bet,
                            "reason": f"negative EV ({live_odds:.2f}, edge {live_edge:.1f}%)",
                            "live_odds": live_odds,
                            "live_edge": live_edge,
                        },
                    )
                    self.stats["skipped"] += 1
                    continue

                # Ready — wait for interceptor or skip, polling live price
                self.state = STATE_READY
                self._bet_intercepted_event.clear()
                self._skip_event.clear()
                self._intercepted_body = None
                self._intercepted_request_body = None
                self._broadcaster.publish(
                    "bet_ready",
                    {
                        "bet": bet,
                        "provider_id": pid,
                        "prep_ok": prep_result.status == "prepped",
                        "live_odds": live_odds,
                        "live_edge": live_edge,
                        "prep_reason": prep_result.reason,
                    },
                )

                # Poll live price every 3s while waiting for placement/skip
                _PRICE_POLL_INTERVAL = 3.0
                _last_live_odds = live_odds
                while not self._bet_intercepted_event.is_set() and not self._skip_event.is_set():
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(
                                asyncio.wait(
                                    [
                                        asyncio.ensure_future(self._bet_intercepted_event.wait()),
                                        asyncio.ensure_future(self._skip_event.wait()),
                                    ],
                                    return_when=asyncio.FIRST_COMPLETED,
                                )
                            ),
                            timeout=_PRICE_POLL_INTERVAL,
                        )
                        break  # One of the events fired
                    except asyncio.TimeoutError:
                        pass  # Poll live price below

                    # Poll live price
                    if hasattr(workflow, "check_live_price"):
                        try:
                            lo, le = await workflow.check_live_price(page, bet_ns)
                            if lo is not None and lo != _last_live_odds:
                                _last_live_odds = lo
                                live_odds = lo
                                live_edge = le
                                self._broadcaster.publish(
                                    "live_price",
                                    {
                                        "event_id": bet.get("event_id", ""),
                                        "market": bet.get("market", ""),
                                        "outcome": bet.get("outcome", ""),
                                        "provider_id": pid,
                                        "live_odds": lo,
                                        "live_edge": le,
                                        "fair_odds": bet.get("fair_odds"),
                                    },
                                )
                        except Exception:
                            pass

                if self._bet_intercepted_event.is_set():
                    self.state = STATE_PLACING
                    try:
                        await self._handle_placement(bet, pid, workflow, page, prep_result, stake)
                    except Exception:
                        logger.exception(f"[Runner:{pid}] Recording failed")
                        self._broadcaster.publish("bet_error", {"bet": bet, "reason": "record_exception"})
                        self.stats["skipped"] += 1
                else:
                    self._broadcaster.publish("bet_skipped", {"bet": bet, "reason": "user_skip"})
                    self.stats["skipped"] += 1

            # Done
            self._broadcaster.publish("provider_complete", {"provider_id": pid})
            logger.info(f"[Runner:{pid}] Complete — {self.stats}")

        except asyncio.CancelledError:
            logger.info(f"[Runner:{pid}] Cancelled")
        except Exception:
            logger.exception(f"[Runner:{pid}] Unhandled error")
        finally:
            self.state = STATE_IDLE
            self.current_bet = None

    # ------------------------------------------------------------------
    # Placement handling — extracted from PlayLoop._run() inner block
    # ------------------------------------------------------------------

    async def _handle_placement(self, bet: dict, pid: str, workflow, page, prep_result, stake: float) -> None:
        provider_bet_id = None
        actual_odds = prep_result.actual_odds
        actual_stake = prep_result.actual_stake
        requested_stake = stake

        if self._intercepted_body:
            # Validate placement status
            if hasattr(workflow, "parse_placement_status"):
                pstatus = workflow.parse_placement_status(self._intercepted_body)
                if not pstatus["success"]:
                    err = pstatus.get("error", "unknown error")
                    self._broadcaster.publish("bet_failed", {"bet": bet, "reason": err})
                    self.stats["skipped"] += 1
                    return

            provider_bet_id = workflow.parse_placement_response(self._intercepted_body)
            if hasattr(workflow, "parse_placement_details"):
                details = workflow.parse_placement_details(self._intercepted_body)
                if details.get("actual_stake"):
                    actual_stake = details["actual_stake"]
                if details.get("actual_odds"):
                    actual_odds = details["actual_odds"]
            if actual_stake == requested_stake and self._intercepted_request_body:
                if hasattr(workflow, "parse_placement_request_stake"):
                    req_stake = workflow.parse_placement_request_stake(self._intercepted_request_body)
                    if req_stake:
                        actual_stake = req_stake

            if actual_stake and requested_stake and actual_stake < requested_stake * 0.9:
                self._broadcaster.publish(
                    "stake_limited",
                    {
                        "bet": bet,
                        "provider_id": pid,
                        "requested_stake": requested_stake,
                        "actual_stake": actual_stake,
                    },
                )

        # Autonomous placement (Pinnacle)
        _balance_synced = False
        if not self._intercepted_body and getattr(workflow, "autonomous_placement", False):
            bet_ns = _bet_ns(bet)
            api_result = await workflow.place_bet(page, bet_ns, stake)
            if api_result.status == "placed":
                result = api_result
                try:
                    new_bal = await workflow.sync_balance(page)
                    if new_bal >= 0:
                        await self._post_balance(pid, new_bal)
                        _balance_synced = True
                except Exception:
                    pass
            elif api_result.status == "skipped":
                self._broadcaster.publish("bet_skipped", {"bet": bet, "reason": api_result.reason})
                self.stats["skipped"] += 1
                return
            else:
                self._broadcaster.publish("bet_failed", {"bet": bet, "reason": api_result.reason})
                self.stats["skipped"] += 1
                return
        else:
            result = PlacementResult(
                status="placed",
                bet_id=provider_bet_id or 0,
                actual_odds=actual_odds,
                actual_stake=actual_stake,
                reason="intercepted" if self._intercepted_body else "manual",
            )

        placed_count = self._placed_today.get(pid, 0) + 1
        self._broadcaster.publish(
            "bet_placed",
            {
                "bet": bet,
                "status": result.status,
                "actual_odds": result.actual_odds,
                "actual_stake": result.actual_stake,
                "placed_today": placed_count,
                "daily_cap": DAILY_BET_CAP,
            },
        )
        self.stats["placed"] += 1
        self._placed_today[pid] = self._placed_today.get(pid, 0) + 1
        await self._record_bet(bet, result)
        self._block_event_market(bet)
        if not _balance_synced:
            cached_bal = self._browser.provider_data.get(pid, {}).get("balance")
            if cached_bal is not None:
                await self._post_balance(pid, cached_bal)

    # ------------------------------------------------------------------
    # Helpers — moved from PlayLoop
    # ------------------------------------------------------------------

    async def _wait_for_login(self, workflow, page) -> bool:
        await asyncio.sleep(2)
        elapsed = 2.0
        while elapsed < LOGIN_TIMEOUT:
            # 0. Workflow check_login is authoritative — always try it first
            try:
                wf_login = await workflow.check_login(page)
                if wf_login:
                    bal = await workflow.sync_balance(page)
                    self._browser.provider_data.setdefault(workflow.provider_id, {}).update(
                        {"logged_in": True, "balance": bal if bal >= 0 else None, "source": "workflow_check"}
                    )
                    self._broadcaster.publish(
                        "login_detected", {"provider_id": workflow.provider_id, "balance": bal if bal >= 0 else None}
                    )
                    logger.info(f"[Runner:{self.provider_id}] Login detected via workflow (balance: {bal})")
                    return True
            except Exception:
                pass
            # 1. Check intercepted data (but verify with DOM to avoid stale state)
            if self._browser.is_logged_in(workflow.provider_id):
                # Double-check with DOM to avoid false positives from stale interceptor data
                try:
                    dom_result = await self._browser.check_login_dom(workflow.provider_id)
                    if dom_result.get("logged_in"):
                        bal = self._browser.get_balance(workflow.provider_id) or dom_result.get("balance")
                        self._broadcaster.publish(
                            "login_detected", {"provider_id": workflow.provider_id, "balance": bal}
                        )
                        logger.info(f"[Runner:{self.provider_id}] Login confirmed (interceptor + DOM, balance: {bal})")
                        return True
                except Exception:
                    # DOM check failed but interceptor says logged in — trust interceptor for non-polymarket
                    if workflow.provider_id != "polymarket":
                        bal = self._browser.get_balance(workflow.provider_id)
                        self._broadcaster.publish(
                            "login_detected", {"provider_id": workflow.provider_id, "balance": bal}
                        )
                        return True
            # 2. DOM scrape fallback (browser-level, different from workflow check_login)
            try:
                dom_result = await self._browser.check_login_dom(workflow.provider_id)
                if dom_result.get("logged_in"):
                    self._broadcaster.publish(
                        "login_detected",
                        {"provider_id": workflow.provider_id, "balance": dom_result.get("balance")},
                    )
                    return True
            except Exception:
                pass
            await asyncio.sleep(LOGIN_POLL_INTERVAL)
            elapsed += LOGIN_POLL_INTERVAL
            self._broadcaster.publish(
                "login_waiting",
                {"provider_id": workflow.provider_id, "elapsed": round(elapsed), "timeout": LOGIN_TIMEOUT},
            )
        return False

    @staticmethod
    async def _is_event_closed(page) -> bool:
        try:
            await asyncio.sleep(1.5)
            text = await page.evaluate(
                """() => {
                const main = document.querySelector('main, [class*="content"], [class*="event"]') || document.body;
                return (main.innerText || '').substring(0, 3000).toLowerCase();
            }"""
            )
            closed_phrases = [
                "avslutat",
                "avslutad",
                "event has ended",
                "event is over",
                "event closed",
                "market closed",
                "market suspended",
                "no longer available",
                "inte tillgänglig",
            ]
            return any(phrase in text for phrase in closed_phrases)
        except Exception:
            return False

    async def _detect_pending(self, provider_id: str, workflow, page) -> None:
        """Detect settled bets and broadcast to UI — does NOT auto-record."""
        pending_bets = await self._fetch_pending(provider_id)

        # Polymarket: DOM-based claim + redeem + match positions against pending
        # bets via API proxy. settle_all can't be used — it does direct DB ops.
        if hasattr(workflow, "claim_banner") and hasattr(workflow, "redeem_all"):
            self.state = STATE_SETTLING
            self._broadcaster.publish("settling_pending", {"provider_id": provider_id})
            try:
                # Navigate to portfolio positions page
                if "/portfolio" not in (page.url or "") or "tab=history" in (page.url or ""):
                    await page.goto(
                        "https://polymarket.com/portfolio?tab=positions",
                        wait_until="domcontentloaded",
                        timeout=15000,
                    )
                    await asyncio.sleep(4)

                # Scrape positions BEFORE redeeming (status visible: WON/LOST)
                positions = []
                if hasattr(workflow, "scrape_portfolio"):
                    positions = await workflow.scrape_portfolio(page)
                    logger.info(f"[Runner:{provider_id}] Scraped {len(positions)} positions")

                # Match scraped positions against pending bets to build settlements
                settlements = []
                if pending_bets and positions:
                    settlements = self._match_polymarket_settlements(pending_bets, positions)
                    logger.info(f"[Runner:{provider_id}] Matched {len(settlements)} settlements")

                # Click Claim banner if present
                claim_result = await workflow.claim_banner(page)
                if claim_result.get("claimed"):
                    logger.info(f"[Runner:{provider_id}] Claimed: {claim_result.get('amount')}")
                    await asyncio.sleep(2)

                # Click Redeem buttons for finished positions
                redeem_result = await workflow.redeem_all(page)
                logger.info(f"[Runner:{provider_id}] Redeem: {redeem_result}")

                # Record settlements to DB via API proxy
                if settlements:
                    await self._record_settlements(provider_id, settlements)
                    logger.info(f"[Runner:{provider_id}] Recorded {len(settlements)} settlements to DB")
                    self._broadcaster.publish(
                        "settlements_confirmed",
                        {"provider_id": provider_id, "settlements": settlements},
                    )

                # Sync balance after claim/redeem
                try:
                    balance = await workflow.sync_balance(page)
                    if balance >= 0:
                        await self._post_balance(provider_id, balance)
                        logger.info(f"[Runner:{provider_id}] Balance synced: ${balance:.2f}")
                except Exception:
                    pass

                self._broadcaster.publish(
                    "settling_done",
                    {
                        "provider_id": provider_id,
                        "pending_count": len(pending_bets),
                        "settled_count": len(settlements),
                        "claim": claim_result,
                        "redeem": redeem_result,
                    },
                )
            except Exception:
                logger.exception(f"[Runner:{provider_id}] DOM settlement failed")
            return

        # Always sync history — provider is source of truth.
        # DB may have fewer bets (manual bets, bets placed before mirror existed).
        self.state = STATE_SETTLING
        self._broadcaster.publish("settling_pending", {"provider_id": provider_id})

        from . import stream_registry

        stream = stream_registry.get(provider_id)
        if stream and stream.is_history_fresh():
            raw_history = stream.get_history()
        else:
            try:
                raw_history = await workflow.sync_history(page)
            except Exception:
                logger.exception(f"[Runner:{provider_id}] sync_history failed")
                return

        history = [
            {
                "odds": e.odds,
                "stake": e.stake,
                "status": e.status,
                "payout": e.payout,
                "provider_bet_id": e.provider_bet_id,
                "event_name": e.event_name,
                "market": e.market,
                "outcome": e.outcome,
            }
            for e in raw_history
        ]

        # Detect settlements — broadcast to UI for user review, don't auto-record
        if pending_bets:
            settlements = _detect_settlements(pending_bets, history)
            if settlements:
                logger.info(f"[Runner:{provider_id}] {len(settlements)} settlements detected — broadcasting for review")
                self._broadcaster.publish(
                    "settlements_detected",
                    {
                        "provider_id": provider_id,
                        "pending_bets": pending_bets,
                        "settlements": settlements,
                    },
                )
            else:
                logger.info(f"[Runner:{provider_id}] {len(pending_bets)} DB pending — all still open")

        # Record unknown open bets from provider that aren't in DB
        await self._record_unknown_open_bets(provider_id, history, pending_bets)

    @staticmethod
    def _match_polymarket_settlements(pending_bets: list[dict], positions: list[dict]) -> list[dict]:
        """Match scraped portfolio positions against pending bets by fuzzy name.

        Returns list of {bet_id, result} dicts for _record_settlements.
        Uses the same fuzzy matching as settle_all but without DB imports.
        """
        from rapidfuzz import fuzz

        settlements = []
        matched_ids: set[int] = set()

        for pos in positions:
            status = pos.get("status", "open")
            if status not in ("won", "lost"):
                continue

            pos_text = pos.get("full_text", "") or pos.get("market", "")
            if not pos_text:
                continue

            best_match = None
            best_score = 0
            for bet in pending_bets:
                bet_id = bet.get("bet_id") or bet.get("id")
                if not bet_id or bet_id in matched_ids:
                    continue

                event_name = bet.get("event_name", "") or bet.get("event", "")
                if not event_name:
                    # Build from home/away
                    h = bet.get("home_team", "") or bet.get("display_home", "")
                    a = bet.get("away_team", "") or bet.get("display_away", "")
                    event_name = f"{h} vs {a}" if h and a else h or a

                if not event_name:
                    continue

                score = max(
                    fuzz.partial_ratio(pos_text.lower(), event_name.lower()),
                    fuzz.token_set_ratio(pos_text.lower(), event_name.lower()),
                )
                if score > best_score and score >= 55:
                    best_score = score
                    best_match = bet

            if best_match:
                bet_id = best_match.get("bet_id") or best_match.get("id")
                matched_ids.add(bet_id)
                settlements.append({"bet_id": bet_id, "result": status})
                logger.info(
                    f"[Polymarket] Matched position '{pos_text[:50]}' → bet #{bet_id} ({status}, score={best_score})"
                )

        return settlements

    async def _reconcile_open_bets(self, provider_id: str, workflow, page) -> int:
        if not hasattr(workflow, "fetch_positions"):
            return 0
        try:
            positions = await workflow.fetch_positions(page)
        except Exception:
            return 0
        if not positions:
            return 0

        pending = await self._fetch_pending(provider_id)
        cluster = _PROVIDER_TO_CLUSTER.get(provider_id)
        if cluster:
            for sibling in _CLUSTER_MEMBERS.get(cluster, []):
                if sibling != provider_id:
                    sib_pending = await self._fetch_pending(sibling)
                    pending.extend(sib_pending)

        known_keys = {(round(b["odds"], 2), round(b["stake"], 1)) for b in pending}
        new_count = 0
        for pos in positions:
            key = (round(pos.odds, 2), round(pos.stake, 1))
            if key not in known_keys:
                logger.info(
                    f"[Runner:{provider_id}] Unrecognized open bet: "
                    f"{pos.event_name} {pos.outcome} @ {pos.odds} {pos.stake}"
                )
                self._broadcaster.publish(
                    "unrecognized_bet",
                    {
                        "provider_id": provider_id,
                        "event_name": pos.event_name,
                        "outcome": pos.outcome,
                        "odds": pos.odds,
                        "stake": pos.stake,
                        "provider_bet_id": pos.provider_bet_id,
                    },
                )
                new_count += 1
            else:
                known_keys.discard(key)
        return new_count

    async def _record_unknown_open_bets(self, provider_id: str, history: list[dict], db_pending: list[dict]) -> None:
        """Record open bets from provider history that aren't in the DB.

        Provider is source of truth — DB may be missing bets placed manually
        or before the mirror existed. Records them so settlement works later.
        """
        # Build set of known (odds, stake) pairs from DB pending (including cluster siblings)
        known_keys: set[tuple[float, float]] = set()
        for b in db_pending:
            known_keys.add((round(float(b.get("odds", 0) or 0), 2), round(float(b.get("stake", 0) or 0), 1)))

        cluster = _PROVIDER_TO_CLUSTER.get(provider_id)
        if cluster:
            for sibling in _CLUSTER_MEMBERS.get(cluster, []):
                if sibling != provider_id:
                    sib_pending = await self._fetch_pending(sibling)
                    for b in sib_pending:
                        known_keys.add(
                            (round(float(b.get("odds", 0) or 0), 2), round(float(b.get("stake", 0) or 0), 1))
                        )

        recorded = 0
        for entry in history:
            if entry.get("status") != "pending":
                continue
            key = (round(float(entry.get("odds", 0) or 0), 2), round(float(entry.get("stake", 0) or 0), 1))
            if key in known_keys:
                known_keys.discard(key)
                continue

            # Unknown open bet — record to DB
            payload = {
                "event_id": "",
                "provider_id": provider_id,
                "market": entry.get("market", ""),
                "outcome": entry.get("outcome", ""),
                "odds": entry.get("odds", 0),
                "stake": entry.get("stake", 0),
                "is_bonus": False,
            }
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        f"{self._proxy_url}/api/bets",
                        json=payload,
                        headers={_AUTH_HEADER: _AUTH_VALUE},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    logger.info(
                        f"[Runner:{provider_id}] Recorded unknown open bet: "
                        f"{entry.get('event_name')} {entry.get('outcome')} "
                        f"@ {entry.get('odds')} stake={entry.get('stake')} → bet #{data.get('bet_id', '?')}"
                    )
                    recorded += 1
            except Exception:
                logger.warning(
                    f"[Runner:{provider_id}] Failed to record unknown bet: "
                    f"{entry.get('event_name')} @ {entry.get('odds')}"
                )

        if recorded:
            self._broadcaster.publish(
                "unknown_bets_recorded",
                {"provider_id": provider_id, "count": recorded},
            )

    async def _fetch_pending(self, provider_id: str) -> list[dict]:
        url = f"{self._proxy_url}/api/opportunities/play/pending-bets"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers={_AUTH_HEADER: _AUTH_VALUE})
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return []
        for prov in data.get("providers", []):
            if prov.get("provider_id") == provider_id:
                return prov.get("bets", [])
        return []

    async def _fetch_placed_today(self, provider_id: str) -> None:
        url = f"{self._proxy_url}/api/opportunities/play/batch"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json={}, headers={_AUTH_HEADER: _AUTH_VALUE})
                resp.raise_for_status()
                data = resp.json()
            placed = data.get("placed_today", {})
            self._placed_today.update(placed)
        except Exception:
            logger.warning(f"[Runner:{provider_id}] failed to fetch placed_today")

    async def _record_settlements(self, provider_id: str, settlements: list[dict]) -> None:
        url = f"{self._proxy_url}/api/opportunities/play/settle-batch"
        batch = [
            {"bet_id": s["bet_id"], "result": s["result"]} for s in settlements if s.get("bet_id") and s.get("result")
        ]
        if not batch:
            return
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json=batch, headers={_AUTH_HEADER: _AUTH_VALUE})
                resp.raise_for_status()
        except Exception:
            logger.exception(f"[Runner:{provider_id}] Failed to record settlements")

    async def _post_balance(self, provider_id: str, balance: float) -> None:
        url = f"{self._proxy_url}/api/bankroll/set/{provider_id}"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json={"balance": balance}, headers={_AUTH_HEADER: _AUTH_VALUE})
                resp.raise_for_status()
        except Exception:
            pass

    async def _record_bet(self, bet: dict[str, Any], result) -> None:
        url = f"{self._proxy_url}/api/bets"
        payload = {
            "event_id": bet.get("event_id", ""),
            "provider_id": bet.get("provider_id", ""),
            "market": bet.get("market", ""),
            "outcome": bet.get("outcome", ""),
            "odds": result.actual_odds or bet.get("odds", 0),
            "stake": result.actual_stake or bet.get("stake", 0),
            "point": bet.get("point"),
            "is_bonus": bet.get("is_bonus", False),
            "start_time": bet.get("start_time"),
        }
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(url, json=payload, headers={_AUTH_HEADER: _AUTH_VALUE})
                    resp.raise_for_status()
                    data = resp.json()
                    logger.info(f"[Runner:{self.provider_id}] Recorded bet {data.get('bet_id', '?')}")
                    return
            except Exception:
                logger.exception(f"[Runner:{self.provider_id}] Failed to record bet (attempt {attempt + 1}/3)")
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
        logger.error(f"[Runner:{self.provider_id}] Bet lost after 3 attempts: {payload}")
