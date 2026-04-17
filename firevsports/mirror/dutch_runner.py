"""DutchRunner — Dutch arbitrage play loop for limited providers.

Places the anchor (+EV) leg on the limited provider, then auto-hedges
on unlimited providers (Pinnacle, Polymarket, Cloudbet) for guaranteed profit.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any

import httpx

from .play_loop import (
    _AUTH_HEADER,
    _AUTH_VALUE,
    COUNTER_PROVIDERS,
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
from .workflows import get_workflow
from .workflows.base import PlacementResult

if TYPE_CHECKING:
    from collections.abc import Callable

    from .browser import MirrorBrowser
    from .sse import MirrorBroadcaster

logger = logging.getLogger(__name__)

# How often to re-fetch Dutch opps from the server API
_OPP_REFRESH_INTERVAL = 30.0


class DutchRunner:
    """Runs the Dutch arbitrage play loop for a single limited provider."""

    def __init__(
        self,
        provider_id: str,
        browser: MirrorBrowser,
        broadcaster: MirrorBroadcaster,
        proxy_url: str,
        block_event_market: Callable[[dict], None],
        is_blocked: Callable[[dict], bool],
        placed_today: dict[str, int],
    ):
        self.provider_id = provider_id
        self._browser = browser
        self._broadcaster = broadcaster
        self._proxy_url = proxy_url.rstrip("/")
        self._block_event_market = block_event_market
        self._is_blocked = is_blocked
        self._placed_today = placed_today

        # Per-runner state
        self.state: str = STATE_IDLE
        self.current_bet: dict | None = None
        self.stats: dict = {"placed": 0, "skipped": 0, "hedged": 0, "unhedged": 0, "total": 0}

        # Async events
        self._bet_intercepted_event = asyncio.Event()
        self._skip_event = asyncio.Event()
        self._intercepted_body: dict | None = None
        self._intercepted_request_body: dict | None = None

        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Public API (same interface as ProviderRunner)
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name=f"dutch_{self.provider_id}")

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
            logger.info(f"[Dutch:{self.provider_id}] Bet intercepted (state={self.state})")
            self._intercepted_body = body
            self._intercepted_request_body = request_body
            self._bet_intercepted_event.set()
        else:
            logger.warning(f"[Dutch:{self.provider_id}] Bet intercepted in state={self.state} — ignoring")

    def get_status(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "state": self.state,
            "current_bet": self.current_bet,
            "stats": self.stats,
            "placed_today": self._placed_today.get(self.provider_id, 0),
            "mode": "dutch",
        }

    # ------------------------------------------------------------------
    # Fetch Dutch opportunities from server API
    # ------------------------------------------------------------------

    async def _fetch_dutch_opps(self) -> list[dict]:
        """Fetch Dutch opportunities anchored on this provider."""
        counter_csv = ",".join(COUNTER_PROVIDERS)
        url = (
            f"{self._proxy_url}/api/opportunities/dutch-workflow"
            f"?providers={self.provider_id}&counterpart_providers={counter_csv}"
        )
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers={_AUTH_HEADER: _AUTH_VALUE})
                resp.raise_for_status()
                data = resp.json()
            opps = data.get("opportunities", [])
            # Filter to positive guaranteed profit only
            opps = [o for o in opps if o.get("guaranteed_profit_pct", 0) > 0]
            # Sort by guaranteed profit descending
            opps.sort(key=lambda o: o.get("guaranteed_profit_pct", 0), reverse=True)
            return opps
        except Exception:
            logger.exception(f"[Dutch:{self.provider_id}] Failed to fetch Dutch opps")
            return []

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        self.state = STATE_PROVIDER_OPENING
        pid = self.provider_id
        logger.info(f"[Dutch:{pid}] Starting Dutch runner")

        try:
            workflow = get_workflow(pid)

            # 1. Find tab
            self._broadcaster.publish("provider_opening", {"provider_id": pid, "mode": "dutch"})
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
                logger.warning(f"[Dutch:{pid}] No tab found — stopping")
                self._broadcaster.publish("provider_skipped", {"provider_id": pid, "reason": "no_tab"})
                return

            # 2. Wait for login
            self.state = STATE_LOGIN_WAITING
            self._broadcaster.publish("login_waiting", {"provider_id": pid})
            logged_in = await self._wait_for_login(workflow, page)
            if not logged_in:
                logger.warning(f"[Dutch:{pid}] Login timeout — stopping")
                self._broadcaster.publish("provider_skipped", {"provider_id": pid, "reason": "login_timeout"})
                return

            # 3. Settle pending bets
            await self._detect_pending(pid, workflow, page)

            # 4. Check daily cap
            if pid not in UNCAPPED_PROVIDERS:
                await self._fetch_placed_today(pid)
                placed = self._placed_today.get(pid, 0)
                if placed >= DAILY_BET_CAP:
                    logger.info(f"[Dutch:{pid}] At daily cap ({placed}/{DAILY_BET_CAP})")
                    self._broadcaster.publish(
                        "provider_complete",
                        {"provider_id": pid, "reason": f"daily cap ({placed}/{DAILY_BET_CAP})"},
                    )
                    return

            # 5. Dutch bet loop
            logger.info(f"[Dutch:{pid}] Entering Dutch bet loop")
            while True:
                if pid not in UNCAPPED_PROVIDERS:
                    placed = self._placed_today.get(pid, 0)
                    if placed >= DAILY_BET_CAP:
                        self._broadcaster.publish(
                            "provider_complete",
                            {"provider_id": pid, "reason": f"daily cap ({placed}/{DAILY_BET_CAP})"},
                        )
                        break

                # Fetch fresh Dutch opps
                opps = await self._fetch_dutch_opps()
                if not opps:
                    logger.info(f"[Dutch:{pid}] No Dutch opps available — done")
                    break

                placed_any = False
                for opp in opps:
                    if self._is_blocked(opp):
                        continue

                    # Find the anchor leg (this provider) and counter-legs
                    anchor_leg = None
                    counter_legs = []
                    for leg in opp.get("legs", []):
                        if leg.get("provider") == pid:
                            anchor_leg = leg
                        else:
                            counter_legs.append(leg)

                    if not anchor_leg or not counter_legs:
                        continue

                    # Phase 1: Pre-validate counter-leg odds
                    counter_plan = self._build_counter_plan(opp, counter_legs)
                    if not counter_plan:
                        logger.info(
                            f"[Dutch:{pid}] No viable counter-legs for "
                            f"{opp.get('home_team')} v {opp.get('away_team')} — skipping"
                        )
                        continue

                    # Build anchor bet dict (compatible with ProviderRunner bet format)
                    anchor_bet = self._opp_to_bet(opp, anchor_leg)
                    self.stats["total"] += 1
                    self.current_bet = anchor_bet

                    # Phase 2: Navigate and prep anchor leg
                    self.state = STATE_NAVIGATING
                    workflow = get_workflow(pid)
                    page = await workflow.find_tab(self._browser.context) if self._browser.context else None
                    if page is None:
                        logger.warning(f"[Dutch:{pid}] Lost tab — skipping")
                        self.stats["skipped"] += 1
                        continue

                    bet_ns = _bet_ns(anchor_bet)
                    nav_ok = await workflow.navigate_to_event(page, bet_ns)
                    if not nav_ok:
                        logger.warning(f"[Dutch:{pid}] Navigation failed — skipping")
                        self._broadcaster.publish("bet_skipped", {"bet": anchor_bet, "reason": "navigation_failed"})
                        self.stats["skipped"] += 1
                        continue

                    # Prep betslip
                    balance = self._browser.provider_data.get(pid, {}).get("balance")
                    stake = self._calc_anchor_stake(opp, anchor_leg, balance)
                    anchor_bet["stake"] = stake
                    bet_ns.stake = stake
                    prep_result = await workflow.prep_betslip(page, bet_ns, stake)

                    # Check live price
                    live_odds = prep_result.actual_odds
                    if hasattr(workflow, "check_live_price"):
                        try:
                            lo, _le = await workflow.check_live_price(page, bet_ns)
                            if lo is not None:
                                live_odds = lo
                        except Exception:
                            pass

                    # Broadcast dutch_bet_ready with full opp context
                    self.state = STATE_READY
                    self._bet_intercepted_event.clear()
                    self._skip_event.clear()
                    self._intercepted_body = None
                    self._intercepted_request_body = None

                    dutch_group_id = uuid.uuid4().hex[:12]
                    self._broadcaster.publish(
                        "dutch_bet_ready",
                        {
                            "bet": anchor_bet,
                            "provider_id": pid,
                            "prep_ok": prep_result.status == "prepped",
                            "live_odds": live_odds,
                            "counter_plan": counter_plan,
                            "guaranteed_profit_pct": opp.get("guaranteed_profit_pct", 0),
                            "dutch_group_id": dutch_group_id,
                        },
                    )

                    # Wait for user confirm or skip
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
                                timeout=3.0,
                            )
                            break
                        except asyncio.TimeoutError:
                            pass

                    if self._skip_event.is_set():
                        self._broadcaster.publish("bet_skipped", {"bet": anchor_bet, "reason": "user_skip"})
                        self.stats["skipped"] += 1
                        continue

                    if not self._bet_intercepted_event.is_set():
                        continue

                    # Phase 2b: Record anchor placement
                    self.state = STATE_PLACING
                    anchor_result = await self._handle_anchor_placement(
                        anchor_bet, pid, workflow, page, prep_result, stake
                    )
                    if not anchor_result:
                        self.stats["skipped"] += 1
                        continue

                    self.stats["placed"] += 1
                    self._placed_today[pid] = self._placed_today.get(pid, 0) + 1
                    self._block_event_market(anchor_bet)

                    # Record anchor bet to DB
                    await self._record_bet(anchor_bet, anchor_result, dutch_group_id)

                    # Phase 3: Auto-hedge counter-legs
                    hedge_ok = await self._place_counter_legs(opp, counter_plan, stake, anchor_leg, dutch_group_id)
                    if hedge_ok:
                        self.stats["hedged"] += 1
                        self._broadcaster.publish(
                            "dutch_complete",
                            {
                                "dutch_group_id": dutch_group_id,
                                "provider_id": pid,
                                "guaranteed_profit_pct": opp.get("guaranteed_profit_pct", 0),
                            },
                        )
                    else:
                        self.stats["unhedged"] += 1

                    placed_any = True
                    break  # Re-fetch fresh opps after each placement

                if not placed_any:
                    logger.info(f"[Dutch:{pid}] No viable opps in batch — done")
                    break

            # Done
            self._broadcaster.publish("provider_complete", {"provider_id": pid, "mode": "dutch"})
            logger.info(f"[Dutch:{pid}] Complete — {self.stats}")

        except asyncio.CancelledError:
            logger.info(f"[Dutch:{pid}] Cancelled")
        except Exception:
            logger.exception(f"[Dutch:{pid}] Unhandled error")
        finally:
            self.state = STATE_IDLE
            self.current_bet = None

    # ------------------------------------------------------------------
    # Counter-leg planning and placement
    # ------------------------------------------------------------------

    def _build_counter_plan(self, opp: dict, counter_legs: list[dict]) -> list[dict] | None:
        """Build a counter-leg placement plan with fallback providers.

        Returns a list of {outcome, providers: [{provider, odds, stake_pct}, ...]}
        where providers are ordered best-odds-first. Returns None if any outcome
        has no viable counter-provider.
        """
        # Group counter-legs by outcome
        by_outcome: dict[str, list[dict]] = {}
        for leg in counter_legs:
            outcome = leg.get("outcome", "")
            if outcome not in by_outcome:
                by_outcome[outcome] = []
            by_outcome[outcome].append(leg)

        plan = []
        for outcome, legs in by_outcome.items():
            # Sort by odds descending (best odds first = most profit)
            legs.sort(key=lambda l: l.get("odds", 0), reverse=True)
            # Filter to counter-providers only
            viable = [l for l in legs if l.get("provider") in COUNTER_PROVIDERS]
            if not viable:
                return None  # Can't cover this outcome
            plan.append(
                {
                    "outcome": outcome,
                    "providers": [
                        {"provider": l["provider"], "odds": l["odds"], "stake_pct": l.get("stake_pct", 0)}
                        for l in viable
                    ],
                }
            )
        return plan

    async def _place_counter_legs(
        self,
        opp: dict,
        counter_plan: list[dict],
        total_stake: float,
        anchor_leg: dict,
        dutch_group_id: str,
    ) -> bool:
        """Place counter-legs with fallback chain. Returns True if fully hedged."""
        anchor_odds = anchor_leg.get("odds", 1.0)
        # Total payout if anchor wins = anchor_stake * anchor_odds
        # For equal-payout Dutch: each counter-leg stake = total_payout / counter_odds
        anchor_stake = total_stake
        total_payout = anchor_stake * anchor_odds

        all_hedged = True
        for counter in counter_plan:
            outcome = counter["outcome"]
            hedged = False
            for fallback in counter["providers"]:
                counter_pid = fallback["provider"]
                counter_odds = fallback["odds"]
                # Stake so this leg pays total_payout if it wins
                counter_stake = round(total_payout / counter_odds, 2)

                logger.info(
                    f"[Dutch:{self.provider_id}] Hedging {outcome} on {counter_pid} "
                    f"@ {counter_odds} stake={counter_stake}"
                )
                self._broadcaster.publish(
                    "dutch_hedge_placing",
                    {
                        "dutch_group_id": dutch_group_id,
                        "counter_provider": counter_pid,
                        "outcome": outcome,
                        "odds": counter_odds,
                        "stake": counter_stake,
                    },
                )

                result = await self._place_on_provider(counter_pid, opp, outcome, counter_odds, counter_stake)
                if result and result.status == "placed":
                    hedged = True
                    self._broadcaster.publish(
                        "dutch_hedge_placed",
                        {
                            "dutch_group_id": dutch_group_id,
                            "counter_provider": counter_pid,
                            "outcome": outcome,
                            "actual_odds": result.actual_odds,
                            "actual_stake": result.actual_stake,
                        },
                    )
                    # Record counter bet to DB
                    counter_bet = self._opp_to_bet(
                        opp,
                        {
                            "outcome": outcome,
                            "provider": counter_pid,
                            "odds": result.actual_odds or counter_odds,
                            "stake_pct": fallback.get("stake_pct", 0),
                            "fair_odds": anchor_leg.get("fair_odds"),
                        },
                    )
                    counter_bet["stake"] = result.actual_stake or counter_stake
                    counter_result = PlacementResult(
                        status="placed",
                        bet_id=result.bet_id,
                        actual_odds=result.actual_odds or counter_odds,
                        actual_stake=result.actual_stake or counter_stake,
                    )
                    await self._record_bet(counter_bet, counter_result, dutch_group_id)
                    break
                else:
                    reason = result.reason if result else "no_result"
                    logger.warning(f"[Dutch:{self.provider_id}] Hedge failed on {counter_pid}: {reason} — trying next")
                    self._broadcaster.publish(
                        "dutch_hedge_failed",
                        {
                            "dutch_group_id": dutch_group_id,
                            "counter_provider": counter_pid,
                            "outcome": outcome,
                            "reason": reason,
                        },
                    )

            if not hedged:
                all_hedged = False
                logger.error(f"[Dutch:{self.provider_id}] UNHEDGED — could not place {outcome} on any counter-provider")
                self._broadcaster.publish(
                    "dutch_unhedged",
                    {
                        "dutch_group_id": dutch_group_id,
                        "provider_id": self.provider_id,
                        "outcome": outcome,
                        "event": f"{opp.get('home_team')} v {opp.get('away_team')}",
                    },
                )

        return all_hedged

    async def _place_on_provider(
        self,
        counter_pid: str,
        opp: dict,
        outcome: str,
        odds: float,
        stake: float,
    ) -> PlacementResult | None:
        """Place a single counter-leg bet on an autonomous provider."""
        try:
            workflow = get_workflow(counter_pid)
            if not getattr(workflow, "autonomous_placement", False):
                logger.warning(f"[Dutch:{self.provider_id}] {counter_pid} is not autonomous — skipping")
                return PlacementResult(status="failed", bet_id=0, reason="not_autonomous")

            if not self._browser.context:
                return PlacementResult(status="failed", bet_id=0, reason="no_browser")

            page = await workflow.find_tab(self._browser.context)
            if not page:
                return PlacementResult(status="failed", bet_id=0, reason="no_tab")

            # Build a bet namespace for the counter-leg
            counter_bet = self._opp_to_bet(
                opp,
                {
                    "outcome": outcome,
                    "provider": counter_pid,
                    "odds": odds,
                },
            )
            counter_bet["stake"] = stake
            bet_ns = _bet_ns(counter_bet)

            # Navigate to event
            nav_ok = await workflow.navigate_to_event(page, bet_ns)
            if not nav_ok:
                return PlacementResult(status="failed", bet_id=0, reason="navigation_failed")

            # Place bet (autonomous — API/SDK call)
            result = await workflow.place_bet(page, bet_ns, stake)
            return result

        except Exception:
            logger.exception(f"[Dutch:{self.provider_id}] Error placing on {counter_pid}")
            return PlacementResult(status="failed", bet_id=0, reason="exception")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _opp_to_bet(opp: dict, leg: dict) -> dict:
        """Convert a Dutch opp + leg into a bet dict compatible with ProviderRunner."""
        return {
            "event_id": opp.get("event_id", ""),
            "provider_id": leg.get("provider", ""),
            "market": opp.get("market", ""),
            "outcome": leg.get("outcome", ""),
            "point": opp.get("point"),
            "odds": leg.get("odds", 0),
            "fair_odds": leg.get("fair_odds"),
            "edge_pct": leg.get("edge_pct", 0),
            "stake": 0,  # Set later based on balance
            "display_home": opp.get("display_home") or opp.get("home_team", ""),
            "display_away": opp.get("display_away") or opp.get("away_team", ""),
            "sport": opp.get("sport", ""),
            "league": opp.get("league", ""),
            "start_time": opp.get("starts_at"),
            "is_bonus": False,
            "provider_meta": {},  # Filled by navigate_to_event
        }

    @staticmethod
    def _calc_anchor_stake(opp: dict, anchor_leg: dict, balance: float | None) -> float:
        """Calculate anchor leg stake based on stake_pct and provider balance."""
        stake_pct = anchor_leg.get("stake_pct", 0)
        if not stake_pct or stake_pct <= 0:
            stake_pct = 1.0 / len(opp.get("legs", [1]))  # Equal split fallback

        # Use provider balance as the total stake pool
        if balance and balance > 0:
            # Total Dutch stake = balance (drain the account)
            anchor_stake = round(balance * stake_pct, 2)
            # Cap at balance
            return min(anchor_stake, balance)
        return 10.0  # Minimum fallback

    async def _handle_anchor_placement(
        self, bet: dict, pid: str, workflow: Any, page: Any, prep_result: Any, stake: float
    ) -> PlacementResult | None:
        """Handle anchor leg placement from interceptor or autonomous API."""
        provider_bet_id = None
        actual_odds = prep_result.actual_odds
        actual_stake = prep_result.actual_stake

        if self._intercepted_body:
            if hasattr(workflow, "parse_placement_status"):
                pstatus = workflow.parse_placement_status(self._intercepted_body)
                if not pstatus["success"]:
                    err = pstatus.get("error", "unknown error")
                    self._broadcaster.publish("bet_failed", {"bet": bet, "reason": err})
                    return None

            provider_bet_id = workflow.parse_placement_response(self._intercepted_body)
            if hasattr(workflow, "parse_placement_details"):
                details = workflow.parse_placement_details(self._intercepted_body)
                if details.get("actual_stake"):
                    actual_stake = details["actual_stake"]
                if details.get("actual_odds"):
                    actual_odds = details["actual_odds"]
            if actual_stake == stake and self._intercepted_request_body:
                if hasattr(workflow, "parse_placement_request_stake"):
                    req_stake = workflow.parse_placement_request_stake(self._intercepted_request_body)
                    if req_stake:
                        actual_stake = req_stake

        elif getattr(workflow, "autonomous_placement", False):
            bet_ns = _bet_ns(bet)
            api_result = await workflow.place_bet(page, bet_ns, stake)
            if api_result.status != "placed":
                self._broadcaster.publish("bet_failed", {"bet": bet, "reason": api_result.reason})
                return None
            return api_result

        self._broadcaster.publish(
            "bet_placed",
            {
                "bet": bet,
                "status": "placed",
                "actual_odds": actual_odds,
                "actual_stake": actual_stake,
                "placed_today": self._placed_today.get(pid, 0) + 1,
                "daily_cap": DAILY_BET_CAP,
                "mode": "dutch",
            },
        )

        return PlacementResult(
            status="placed",
            bet_id=provider_bet_id or 0,
            actual_odds=actual_odds,
            actual_stake=actual_stake,
            reason="intercepted" if self._intercepted_body else "manual",
        )

    async def _record_bet(self, bet: dict, result: PlacementResult, dutch_group_id: str) -> None:
        """Record a bet to the server DB with dutch group linkage."""
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
            "notes": f"dutch_group:{dutch_group_id}",
        }
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(url, json=payload, headers={_AUTH_HEADER: _AUTH_VALUE})
                    resp.raise_for_status()
                    data = resp.json()
                    logger.info(
                        f"[Dutch:{self.provider_id}] Recorded bet {data.get('bet_id', '?')} (group={dutch_group_id})"
                    )
                    return
            except Exception:
                logger.exception(f"[Dutch:{self.provider_id}] Failed to record bet (attempt {attempt + 1}/3)")
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
        logger.error(f"[Dutch:{self.provider_id}] Bet lost after 3 attempts: {payload}")

    # ------------------------------------------------------------------
    # Reused helpers from ProviderRunner
    # ------------------------------------------------------------------

    async def _wait_for_login(self, workflow: Any, page: Any) -> bool:
        """Wait for user login — same logic as ProviderRunner."""
        await asyncio.sleep(2)
        elapsed = 2.0
        while elapsed < LOGIN_TIMEOUT:
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
                    return True
            except Exception:
                pass
            if self._browser.is_logged_in(workflow.provider_id):
                try:
                    dom_result = await self._browser.check_login_dom(workflow.provider_id)
                    if dom_result.get("logged_in"):
                        bal = self._browser.get_balance(workflow.provider_id) or dom_result.get("balance")
                        self._broadcaster.publish(
                            "login_detected", {"provider_id": workflow.provider_id, "balance": bal}
                        )
                        return True
                except Exception:
                    if workflow.provider_id != "polymarket":
                        bal = self._browser.get_balance(workflow.provider_id)
                        self._broadcaster.publish(
                            "login_detected", {"provider_id": workflow.provider_id, "balance": bal}
                        )
                        return True
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

    async def _detect_pending(self, pid: str, workflow: Any, page: Any) -> None:
        """Detect and broadcast pending settlements — same as ProviderRunner."""
        from .pending_loop import _detect_settlements

        self.state = STATE_SETTLING
        self._broadcaster.publish("settling_pending", {"provider_id": pid})

        pending_bets = await self._fetch_pending(pid)

        from . import stream_registry

        stream = stream_registry.get(pid)
        if stream and stream.is_history_fresh():
            raw_history = stream.get_history()
        else:
            try:
                raw_history = await workflow.sync_history(page)
            except Exception:
                logger.exception(f"[Dutch:{pid}] sync_history failed")
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

        if pending_bets:
            settlements = _detect_settlements(pending_bets, history)
            if settlements:
                logger.info(f"[Dutch:{pid}] {len(settlements)} settlements detected")
                self._broadcaster.publish(
                    "settlements_detected",
                    {"provider_id": pid, "pending_bets": pending_bets, "settlements": settlements},
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
            logger.warning(f"[Dutch:{provider_id}] failed to fetch placed_today")

    async def _post_balance(self, provider_id: str, balance: float) -> None:
        url = f"{self._proxy_url}/api/bankroll/set/{provider_id}"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json={"balance": balance}, headers={_AUTH_HEADER: _AUTH_VALUE})
                resp.raise_for_status()
        except Exception:
            pass
