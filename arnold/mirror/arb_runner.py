"""ArbRunner v2 — semi-auto arb workflow.

Per opp: load all legs in parallel → start SlipOddsStream per leg →
broadcast arb_alignment on every meaningful odds change → wait for the
user to click Place inside the soft mirror tab → on accepted, recompute
counter stakes from actual placed anchor stake/odds → update each
counter slip → wait for the user to click Place inside each counter
mirror tab → record the arb_group → iterate.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any

from .arb_math import (
    is_valid_arb_shape,
    recalc_counter_stakes,
    recalc_profit_pct,
    should_update_stake,
)
from .play_loop import (
    _AUTH_HEADER,
    _AUTH_VALUE,
    _PROVIDER_TO_CLUSTER,
    DAILY_BET_CAP,
    LOGIN_POLL_INTERVAL,
    LOGIN_TIMEOUT,
    STATE_IDLE,
    STATE_LOGIN_WAITING,
    STATE_PROVIDER_OPENING,
    STATE_SETTLING,
    UNCAPPED_PROVIDERS,
    UNLIMITED_PROVIDERS,
    _bet_ns,
)
from .slip_odds_stream import SlipOddsStream
from .workflows import get_workflow
from .workflows.base import PlacementResult

if TYPE_CHECKING:
    from collections.abc import Callable

    from .browser import MirrorBrowser
    from .sse import MirrorBroadcaster

logger = logging.getLogger(__name__)

STATE_LOADING_LEGS = "loading_legs"
STATE_STANDBY = "standby"
STATE_AWAITING_HEDGES = "awaiting_hedges"


def _log_task_exception(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning(f"slip stake update task failed: {exc!r}")


_OPP_FETCH_COOLDOWN = 10.0
_ALIGNMENT_BROADCAST_THROTTLE_S = 0.5
LEG_DRIFT_TOL_PCT = 0.01  # 1% drift tolerance below planned odds → red
RERANK_INTERVAL_S = 5.0
DETHRONE_HYSTERESIS_PCT = 0.5


class ArbRunner:
    """Runs the semi-auto arbitrage play loop for a single soft book.

    Loads all legs in parallel, starts a SlipOddsStream per leg, broadcasts
    arb_alignment continuously, waits for the user to click Place in the soft
    mirror tab, then waits for the user to click Place in each counter tab.
    No auto-hedge — every leg requires a manual mirror click.
    """

    def __init__(
        self,
        provider_id: str,
        browser: MirrorBrowser,
        broadcaster: MirrorBroadcaster,
        proxy_url: str,
        block_event_market: Callable[[dict], None],
        is_blocked: Callable[[dict], bool],
        placed_today: dict[str, int],
        active_providers: list[str] | None = None,
    ):
        self.provider_id = provider_id
        self._browser = browser
        self._broadcaster = broadcaster
        self._proxy_url = proxy_url.rstrip("/")
        self._block_event_market = block_event_market
        self._is_blocked = is_blocked
        self._placed_today = placed_today
        self._active_providers = list(active_providers or [])

        self.state: str = STATE_IDLE
        self.current_opp: dict | None = None
        self.current_arb_group_id: str | None = None
        self.stats: dict = {"placed": 0, "skipped": 0, "rejected": 0, "complete": 0, "total": 0}

        # Anchor (soft) intercept
        self._anchor_event: asyncio.Event = asyncio.Event()
        self._intercepted_body: dict | None = None
        self._intercepted_request_body: dict | None = None
        self._intercepted_while_red: bool = False  # spec §4.2 placed-while-red gate

        # Counter intercepts
        self._counter_events: dict[str, asyncio.Event] = {}
        self._counter_intercepted: dict[str, dict] = {}

        # Per-leg streams
        self._streams: dict[str, SlipOddsStream] = {}
        self._latest_counter_odds: dict[str, float] = {}
        self._counter_legs: list[dict] = []
        self._anchor_stake: float = 0.0
        self._last_alignment_broadcast: float = 0.0
        self._planned_anchor_odds: float = 0.0
        self.current_opp_key: str | None = None
        self._dethroned_to: dict | None = None
        self._current_recomputed_profit_pct: float | None = None

        self._all_green: bool = False

        self._task: asyncio.Task | None = None
        self._top_opp_watcher_task: asyncio.Task | None = None
        self._update_tasks: set[asyncio.Task] = set()

        # Last-known reason the runner went idle / didn't pick an opp.
        # Surfaced via get_status() so the UI can show "betinia: idle (no_opps_in_pool)"
        # instead of a silent dead state.
        self.last_idle_reason: str | None = None
        self.last_skip_counts: dict[str, int] = {}

    # ----- public surface -----

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name=f"arb_{self.provider_id}")

    def stop(self) -> None:
        for s in self._streams.values():
            s.stop()
        self._streams.clear()
        self._counter_events.clear()
        self._counter_intercepted.clear()
        for t in list(self._update_tasks):
            if not t.done():
                t.cancel()
        self._update_tasks.clear()
        if self._top_opp_watcher_task and not self._top_opp_watcher_task.done():
            self._top_opp_watcher_task.cancel()
        self._top_opp_watcher_task = None
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        self.current_opp_key = None
        self._planned_anchor_odds = 0.0
        self._dethroned_to = None
        self._current_recomputed_profit_pct = None
        self._all_green = False
        self.state = STATE_IDLE

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def on_bet_intercepted(self, body: dict, request_body: dict | None = None) -> None:
        """Anchor (soft) leg placement intercepted.

        Per spec §4.2 green-gate: if any leg was red at intercept time,
        flag intercepted_while_red so _stream_and_await_anchor refuses to
        record the bet and emits arb_anchor_rejected with reason='placed_while_red'.
        """
        if self.state in (STATE_STANDBY, STATE_LOADING_LEGS):
            self._intercepted_while_red = not self._all_green
            if self._intercepted_while_red:
                logger.warning(f"[Arb:{self.provider_id}] Anchor intercepted while NOT all-green — will reject")
            else:
                logger.info(f"[Arb:{self.provider_id}] Anchor placement intercepted")
            self._intercepted_body = body
            self._intercepted_request_body = request_body
            self._anchor_event.set()
        else:
            logger.warning(f"[Arb:{self.provider_id}] Anchor intercept in state={self.state} — ignoring")

    def on_counter_bet_intercepted(
        self, counter_provider_id: str, body: dict, request_body: dict | None = None
    ) -> None:
        """Counter leg placement intercepted (called by play_loop router)."""
        if counter_provider_id in self._counter_events:
            logger.info(f"[Arb:{self.provider_id}] Counter {counter_provider_id} intercepted")
            self._counter_intercepted[counter_provider_id] = {"body": body, "request_body": request_body}
            self._counter_events[counter_provider_id].set()
        else:
            logger.warning(
                f"[Arb:{self.provider_id}] Counter intercept for {counter_provider_id} but no event registered"
            )

    def get_status(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "state": self.state,
            "current_opp": self.current_opp,
            "arb_group_id": self.current_arb_group_id,
            "stats": self.stats,
            "placed_today": self._placed_today.get(self.provider_id, 0),
            "mode": "arb",
            "last_idle_reason": self.last_idle_reason,
            "last_skip_counts": dict(self.last_skip_counts),
        }

    # ----- main loop -----

    async def _run(self) -> None:
        self.state = STATE_PROVIDER_OPENING
        pid = self.provider_id
        logger.info(f"[Arb:{pid}] Starting arb runner v2")

        try:
            workflow = get_workflow(pid)

            # 1. Find tab
            self._broadcaster.publish("provider_opening", {"provider_id": pid, "mode": "arb"})
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
                logger.warning(f"[Arb:{pid}] No tab found — stopping")
                self.last_idle_reason = "no_tab"
                self._broadcaster.publish("provider_skipped", {"provider_id": pid, "reason": "no_tab"})
                self._broadcaster.publish(
                    "arb_runner_idle",
                    {"provider_id": pid, "reason": "no_tab", "details": {"domain": workflow.domain}},
                )
                return

            # 2. Wait for login
            self.state = STATE_LOGIN_WAITING
            self._broadcaster.publish("login_waiting", {"provider_id": pid})
            logged_in = await self._wait_for_login(workflow, page)
            if not logged_in:
                logger.warning(f"[Arb:{pid}] Login timeout — stopping")
                self.last_idle_reason = "login_timeout"
                self._broadcaster.publish("provider_skipped", {"provider_id": pid, "reason": "login_timeout"})
                self._broadcaster.publish(
                    "arb_runner_idle",
                    {"provider_id": pid, "reason": "login_timeout", "details": {}},
                )
                return

            # 3. Settle pending bets
            await self._detect_pending(pid, workflow, page)

            # 4. Check daily cap
            if pid not in UNCAPPED_PROVIDERS:
                await self._fetch_placed_today(pid)
                placed = self._placed_today.get(pid, 0)
                if placed >= DAILY_BET_CAP:
                    logger.info(f"[Arb:{pid}] At daily cap ({placed}/{DAILY_BET_CAP})")
                    self._broadcaster.publish(
                        "provider_complete",
                        {"provider_id": pid, "reason": f"daily cap ({placed}/{DAILY_BET_CAP})"},
                    )
                    return

            # 5. Arb bet loop
            logger.info(f"[Arb:{pid}] Entering arb bet loop (v2)")
            skip_counts: dict[str, int] = {}
            while True:
                if pid not in UNCAPPED_PROVIDERS:
                    placed = self._placed_today.get(pid, 0)
                    if placed >= DAILY_BET_CAP:
                        self._broadcaster.publish(
                            "provider_complete",
                            {"provider_id": pid, "reason": f"daily cap ({placed}/{DAILY_BET_CAP})"},
                        )
                        break

                # Fetch fresh arb opps
                opps = await self._fetch_arb_opps()
                if not opps:
                    logger.info(
                        f"[Arb:{pid}] No arb opps available — done. "
                        f"counter_pool={self._counter_pool()} active={self._active_providers}"
                    )
                    self.last_idle_reason = "no_opps_in_pool"
                    self.last_skip_counts = dict(skip_counts)
                    self._broadcaster.publish(
                        "arb_runner_idle",
                        {
                            "provider_id": pid,
                            "reason": "no_opps_in_pool",
                            "details": {
                                "counter_pool": self._counter_pool(),
                                "active_providers": self._active_providers,
                                "skip_counts": skip_counts,
                            },
                        },
                    )
                    break

                placed_any = False
                for opp in opps:
                    if self._is_blocked(opp):
                        skip_counts["blocked"] = skip_counts.get("blocked", 0) + 1
                        continue

                    # Load all legs in parallel
                    self.state = STATE_LOADING_LEGS
                    loaded = await self._load_all_legs(opp)
                    if not loaded:
                        skip_counts["load_failed"] = skip_counts.get("load_failed", 0) + 1
                        continue

                    self.stats["total"] += 1

                    # Stream and await anchor click (with top-opp watcher)
                    self.state = STATE_STANDBY
                    self._top_opp_watcher_task = asyncio.create_task(self._watch_top_opp(), name=f"arb_watch_{pid}")
                    try:
                        anchor_result = await self._stream_and_await_anchor()
                    finally:
                        if self._top_opp_watcher_task and not self._top_opp_watcher_task.done():
                            self._top_opp_watcher_task.cancel()
                            try:
                                await self._top_opp_watcher_task
                            except (asyncio.CancelledError, Exception):
                                pass
                        self._top_opp_watcher_task = None

                    if anchor_result is None:
                        # Either rejected, stopped, or dethroned
                        for s in self._streams.values():
                            s.stop()
                        self._streams.clear()
                        self._counter_events.clear()
                        self._counter_intercepted.clear()
                        if self._dethroned_to is not None:
                            new_opp = self._dethroned_to
                            self._dethroned_to = None
                            # Swap opp inline — fall through to next iteration over a synthetic 1-element list
                            opps = [new_opp]
                            placed_any = True
                            break
                        self.stats["rejected"] += 1
                        continue

                    # Anchor placed — record it
                    self.stats["placed"] += 1
                    self._placed_today[pid] = self._placed_today.get(pid, 0) + 1

                    anchor_bet = self._opp_to_bet(
                        opp, next(l for l in (opp.get("arb_legs") or opp.get("legs", [])) if l.get("provider") == pid)
                    )
                    anchor_bet["stake"] = anchor_result["actual_stake"]
                    anchor_placement = PlacementResult(
                        status="placed",
                        bet_id=0,
                        actual_odds=anchor_result.get("actual_odds"),
                        actual_stake=anchor_result["actual_stake"],
                    )
                    self._block_event_market(anchor_bet)
                    await self._record_bet(anchor_bet, anchor_placement, self.current_arb_group_id or "")

                    # Update counter slips and await hedge clicks.
                    # Counter bets are recorded inside _update_counter_slips_and_await_hedges
                    # (only on successful placements; rejections emit arb_hedge_failed instead).
                    self.state = STATE_AWAITING_HEDGES
                    actual_odds = anchor_result.get("actual_odds") or (
                        next(
                            (
                                l.get("odds", 0)
                                for l in (opp.get("arb_legs") or opp.get("legs", []))
                                if l.get("provider") == pid
                            ),
                            0,
                        )
                    )
                    await self._update_counter_slips_and_await_hedges(anchor_result["actual_stake"], actual_odds)

                    # Clean up streams
                    for s in self._streams.values():
                        s.stop()
                    self._streams.clear()
                    self._counter_events.clear()
                    self._counter_intercepted.clear()

                    placed_any = True
                    break  # Re-fetch fresh opps after each placement

                if not placed_any:
                    logger.info(f"[Arb:{pid}] No viable opps in batch — done. skip_counts={skip_counts}")
                    self.last_idle_reason = "no_viable_opps"
                    self.last_skip_counts = dict(skip_counts)
                    self._broadcaster.publish(
                        "arb_runner_idle",
                        {
                            "provider_id": pid,
                            "reason": "no_viable_opps",
                            "details": {"skip_counts": skip_counts},
                        },
                    )
                    break

                # Cooldown before re-fetching
                await asyncio.sleep(_OPP_FETCH_COOLDOWN)

            # Done
            self._broadcaster.publish("provider_complete", {"provider_id": pid, "mode": "arb"})
            logger.info(f"[Arb:{pid}] Complete — {self.stats}")

        except asyncio.CancelledError:
            logger.info(f"[Arb:{pid}] Cancelled")
        except Exception:
            logger.exception(f"[Arb:{pid}] Unhandled error")
        finally:
            for s in self._streams.values():
                s.stop()
            self._streams.clear()
            self.state = STATE_IDLE
            self.current_opp = None

    # ----- new helpers -----

    async def _load_all_legs(self, opp: dict) -> bool:
        """Navigate + prep every leg in parallel. Returns True on success."""
        legs = opp.get("arb_legs") or opp.get("legs", [])
        if not is_valid_arb_shape(legs, unlimited=set(UNLIMITED_PROVIDERS)):
            self._broadcaster.publish(
                "bet_skipped",
                {"opp": opp, "reason": "invalid_arb_shape (need 1 soft + ≥1 unlimited)"},
            )
            return False

        anchor_leg = next((l for l in legs if l.get("provider") == self.provider_id), None)
        counter_legs = [l for l in legs if l.get("provider") != self.provider_id]
        if not anchor_leg or not counter_legs:
            self._broadcaster.publish(
                "bet_skipped",
                {"opp": opp, "reason": "missing_legs"},
            )
            return False

        # Anchor stake = full balance (capped at site max)
        balance = self._browser.provider_data.get(self.provider_id, {}).get("balance") or 0.0
        anchor_stake = round(balance, 2)  # site-max cap learned later from limit responses
        if anchor_stake <= 0:
            self._broadcaster.publish(
                "bet_skipped",
                {"opp": opp, "reason": "zero_anchor_stake", "balance": balance},
            )
            return False

        anchor_odds = anchor_leg.get("odds", 0)
        counter_odds = [l.get("odds", 0) for l in counter_legs]
        counter_stakes = recalc_counter_stakes(anchor_stake, anchor_odds, counter_odds)

        self._anchor_stake = anchor_stake
        self._counter_legs = counter_legs
        self._planned_anchor_odds = anchor_odds
        for leg, planned_odds in zip(counter_legs, counter_odds):
            leg["_planned_odds"] = planned_odds
        self.current_opp_key = self._compute_opp_key(opp, anchor_leg)
        self._dethroned_to = None
        self._current_recomputed_profit_pct = None
        self.current_opp = opp
        self.current_arb_group_id = uuid.uuid4().hex[:12]

        # Navigate + prep all legs in parallel
        async def _prep_leg(leg: dict, planned_stake: float) -> tuple[str, bool, str]:
            pid = leg["provider"]
            try:
                wf = get_workflow(pid)
                if not self._browser.context:
                    return pid, False, "no_browser_context"
                page = await wf.find_tab(self._browser.context)
                if not page:
                    return pid, False, "no_tab"
                bet = self._opp_to_bet(opp, leg)
                bet["stake"] = planned_stake
                bet_ns = _bet_ns(bet)
                nav_ok = await wf.navigate_to_event(page, bet_ns)
                if not nav_ok:
                    return pid, False, "navigate_failed"
                prep = await wf.prep_betslip(page, bet_ns, planned_stake)
                if prep.status not in ("prepped", "placed"):
                    return pid, False, f"prep_{prep.status}:{getattr(prep, 'reason', '') or 'no_reason'}"
                # Start SlipOddsStream for this leg
                stream = SlipOddsStream(
                    provider_id=pid,
                    workflow=wf,
                    page=page,
                    on_odds_change=lambda o, p=pid: self._on_leg_odds_change(p, o),
                    poll_interval_s=1.0,
                )
                stream.start()
                self._streams[pid] = stream
                return pid, True, "ok"
            except Exception as e:
                logger.exception(f"[Arb:{self.provider_id}] prep failed for {pid}")
                return pid, False, f"exception:{type(e).__name__}"

        prep_results = await asyncio.gather(
            _prep_leg(anchor_leg, anchor_stake),
            *[_prep_leg(l, s) for l, s in zip(counter_legs, counter_stakes)],
        )
        failures = [(p, why) for p, ok, why in prep_results if not ok]
        if failures:
            for s in self._streams.values():
                s.stop()
            self._streams.clear()
            self._broadcaster.publish(
                "bet_skipped",
                {
                    "opp": opp,
                    "reason": "prep_failed",
                    "failures": [{"provider_id": p, "why": why} for p, why in failures],
                },
            )
            logger.info(
                f"[Arb:{self.provider_id}] _load_all_legs prep failed: {', '.join(f'{p}={why}' for p, why in failures)}"
            )
            return False

        # Register counter events
        for leg in counter_legs:
            self._counter_events[leg["provider"]] = asyncio.Event()
        self._counter_intercepted = {}

        self._broadcaster.publish(
            "arb_legs_loaded",
            {
                "arb_group_id": self.current_arb_group_id,
                "legs": [
                    {
                        "provider_id": leg["provider"],
                        "event_id": opp.get("event_id"),
                        "market": opp.get("market"),
                        "outcome": leg.get("outcome"),
                        "planned_stake": s,
                        "planned_odds": leg.get("odds"),
                        "slip_state": "loading",
                    }
                    for leg, s in zip([anchor_leg] + counter_legs, [anchor_stake] + counter_stakes)
                ],
            },
        )
        return True

    def _on_leg_odds_change(self, provider_id: str, odds: float) -> None:
        """Stream callback — recompute alignment, throttle broadcast."""
        if provider_id == self.provider_id:
            anchor_odds = odds
        else:
            self._latest_counter_odds[provider_id] = odds
            anchor_odds = self._streams[self.provider_id].current_odds or 0.0

        counter_odds = [self._latest_counter_odds.get(l["provider"], l.get("odds", 0)) for l in self._counter_legs]
        if anchor_odds <= 0 or any(o <= 0 for o in counter_odds):
            return
        profit = recalc_profit_pct(anchor_odds, counter_odds)
        if profit is None:
            return

        # Update counter slip stakes if drift exceeds threshold
        new_stakes = recalc_counter_stakes(self._anchor_stake, anchor_odds, counter_odds)
        for leg, new_stake in zip(self._counter_legs, new_stakes):
            cur = leg.get("_current_stake", new_stake)
            if should_update_stake(cur, new_stake):
                leg["_current_stake"] = new_stake
                wf = get_workflow(leg["provider"])
                page = self._streams[leg["provider"]].page
                t = asyncio.create_task(wf.update_slip_stake(page, new_stake))
                self._update_tasks.add(t)
                t.add_done_callback(self._update_tasks.discard)
                t.add_done_callback(_log_task_exception)

        # Compute per-leg slip_state
        anchor_state = self._compute_slip_state(self._planned_anchor_odds, anchor_odds)
        counter_states = [
            self._compute_slip_state(leg.get("_planned_odds", leg.get("odds", 0)), live)
            for leg, live in zip(self._counter_legs, counter_odds)
        ]
        all_states = [anchor_state] + counter_states
        all_green = all(s == "green" for s in all_states) and profit > 0
        self._current_recomputed_profit_pct = profit
        self._all_green = all_green

        # Throttle broadcast
        loop = asyncio.get_running_loop()
        now = loop.time()
        if now - self._last_alignment_broadcast >= _ALIGNMENT_BROADCAST_THROTTLE_S:
            self._last_alignment_broadcast = now
            self._broadcaster.publish(
                "arb_alignment",
                {
                    "arb_group_id": self.current_arb_group_id,
                    "profit_pct": round(profit, 3),
                    "current_profit_pct": round(profit, 3),
                    "all_green": all_green,
                    "legs": [
                        {
                            "provider_id": self.provider_id,
                            "current_odds": anchor_odds,
                            "planned_odds": self._planned_anchor_odds,
                            "drift_pct": round((anchor_odds / self._planned_anchor_odds - 1.0) * 100.0, 3)
                            if self._planned_anchor_odds > 0
                            else 0.0,
                            "current_stake": self._anchor_stake,
                            "slip_state": anchor_state,
                        }
                    ]
                    + [
                        {
                            "provider_id": leg["provider"],
                            "current_odds": self._latest_counter_odds.get(leg["provider"], leg.get("odds", 0)),
                            "planned_odds": leg.get("_planned_odds", leg.get("odds", 0)),
                            "drift_pct": round(
                                (
                                    self._latest_counter_odds.get(leg["provider"], leg.get("odds", 0))
                                    / leg.get("_planned_odds", leg.get("odds", 1))
                                    - 1.0
                                )
                                * 100.0,
                                3,
                            )
                            if leg.get("_planned_odds", 0) > 0
                            else 0.0,
                            "current_stake": leg.get("_current_stake", 0),
                            "slip_state": state,
                        }
                        for leg, state in zip(self._counter_legs, counter_states)
                    ],
                },
            )

    async def _stream_and_await_anchor(self) -> dict | None:
        """Wait for the anchor (soft) placement to be intercepted. Returns the placement details or None on reject."""
        self._anchor_event.clear()
        self._intercepted_body = None
        self._intercepted_while_red = False
        # Block forever until the user clicks Place in mirror; cancel via stop().
        await self._anchor_event.wait()

        # Watcher may have set _dethroned_to and fired the event — treat as non-placement.
        if self._dethroned_to is not None:
            return None

        # Spec §4.2 green-gate: refuse to record a placement made while a leg was red.
        # The site placement still went through (we can't stop a user's click), but it
        # is not part of the arb_group from our perspective. Pending-loop reconciliation
        # picks it up later via provider history.
        if self._intercepted_while_red:
            self._broadcaster.publish(
                "arb_anchor_rejected",
                {
                    "arb_group_id": self.current_arb_group_id,
                    "provider_id": self.provider_id,
                    "reason": "placed_while_red",
                },
            )
            return None

        wf = get_workflow(self.provider_id)
        body = self._intercepted_body or {}
        pstatus = wf.parse_placement_status(body) if hasattr(wf, "parse_placement_status") else {"success": True}
        if not pstatus.get("success"):
            self._broadcaster.publish(
                "arb_anchor_rejected",
                {
                    "arb_group_id": self.current_arb_group_id,
                    "provider_id": self.provider_id,
                    "reason": pstatus.get("error", "unknown"),
                },
            )
            return None

        actual_stake = self._anchor_stake
        actual_odds = None
        if hasattr(wf, "parse_placement_details"):
            details = wf.parse_placement_details(body) or {}
            actual_stake = details.get("actual_stake") or actual_stake
            actual_odds = details.get("actual_odds")

        self._broadcaster.publish(
            "arb_anchor_placed",
            {
                "arb_group_id": self.current_arb_group_id,
                "provider_id": self.provider_id,
                "actual_stake": actual_stake,
                "actual_odds": actual_odds,
            },
        )
        return {"actual_stake": actual_stake, "actual_odds": actual_odds, "body": body}

    async def _update_counter_slips_and_await_hedges(
        self, anchor_actual_stake: float, anchor_actual_odds: float
    ) -> bool:
        """Re-derive counter stakes from actual anchor placement; update each counter slip; await placements."""
        # Use latest streamed counter odds (best truth available)
        counter_odds = [self._latest_counter_odds.get(l["provider"], l.get("odds", 0)) for l in self._counter_legs]
        new_stakes = recalc_counter_stakes(anchor_actual_stake, anchor_actual_odds, counter_odds)

        # Update slips in parallel
        async def _push_stake(leg: dict, stake: float) -> None:
            pid = leg["provider"]
            wf = get_workflow(pid)
            page = self._streams[pid].page
            try:
                await wf.update_slip_stake(page, stake)
            except Exception:
                logger.exception(f"[Arb:{self.provider_id}] update_slip_stake failed for {pid}")
            leg["_current_stake"] = stake

        await asyncio.gather(*[_push_stake(l, s) for l, s in zip(self._counter_legs, new_stakes)])

        # Wait for every counter event
        await asyncio.gather(*(ev.wait() for ev in self._counter_events.values()))

        # Inspect each counter placement: emit arb_hedge_failed on rejection,
        # arb_hedge_placed + _record_bet on success (per spec §6).
        for leg in self._counter_legs:
            pid = leg["provider"]
            intercepted = self._counter_intercepted.get(pid, {})
            body = intercepted.get("body", {}) if isinstance(intercepted, dict) else {}
            wf = get_workflow(pid)
            pstatus = wf.parse_placement_status(body) if hasattr(wf, "parse_placement_status") else {"success": True}
            if not pstatus.get("success"):
                self._broadcaster.publish(
                    "arb_hedge_failed",
                    {
                        "arb_group_id": self.current_arb_group_id,
                        "counter_provider": pid,
                        "outcome": leg.get("outcome"),
                        "reason": pstatus.get("error") or "unknown",
                        "max_stake": pstatus.get("max_stake"),
                    },
                )
                continue
            self._broadcaster.publish(
                "arb_hedge_placed",
                {
                    "arb_group_id": self.current_arb_group_id,
                    "counter_provider": pid,
                    "outcome": leg.get("outcome"),
                    "actual_odds": leg.get("odds"),
                    "actual_stake": leg.get("_current_stake"),
                },
            )
            counter_bet = self._opp_to_bet(self.current_opp or {}, leg)
            counter_bet["stake"] = leg.get("_current_stake", 0)
            counter_placement = PlacementResult(
                status="placed",
                bet_id=0,
                actual_odds=leg.get("odds"),
                actual_stake=leg.get("_current_stake", 0),
            )
            await self._record_bet(counter_bet, counter_placement, self.current_arb_group_id or "")

        self._broadcaster.publish(
            "arb_complete",
            {
                "arb_group_id": self.current_arb_group_id,
                "guaranteed_profit_pct": self.current_opp.get("guaranteed_profit_pct") if self.current_opp else None,
            },
        )
        self.stats["complete"] += 1
        return True

    # ----- reused helpers from old ArbRunner -----

    def _counter_pool(self) -> list[str]:
        """Return active providers eligible as counter-legs.

        Excludes self and cluster siblings (they share odds engines — zero edge).
        Includes unlimited providers even if not in active list.
        """
        own_cluster = _PROVIDER_TO_CLUSTER.get(self.provider_id)
        pool: list[str] = []
        seen: set[str] = set()
        for pid in self._active_providers + list(UNLIMITED_PROVIDERS):
            if pid == self.provider_id or pid in seen:
                continue
            other_cluster = _PROVIDER_TO_CLUSTER.get(pid)
            if own_cluster and other_cluster and own_cluster == other_cluster:
                continue  # Sibling — identical odds, no edge
            pool.append(pid)
            seen.add(pid)
        return pool

    async def _fetch_arb_opps(self) -> list[dict]:
        """Fetch arbitrage opportunities anchored on this provider.

        Backend's counterpart_providers query filter is broken (returns 0 opps for
        valid pairs — see Hartberg/LASK regression confirmed 2026-04-26). We instead
        drop the URL filter and post-filter in Python: every non-anchor leg must be
        a member of self._counter_pool() (active providers + UNLIMITED, minus self
        and same-cluster siblings).
        """
        from arnold.http_client import tunnel_client as _tc

        pool = set(self._counter_pool())
        pool.add(self.provider_id)  # anchor provider is always allowed
        try:
            resp = await _tc().get(
                "/api/opportunities/arb-workflow",
                params={"providers": self.provider_id},
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
            opps = data.get("opportunities", [])
            # Post-filter: keep only opps whose legs all live in the allowed set
            filtered = []
            for opp in opps:
                legs = opp.get("arb_legs") or opp.get("legs", [])
                if not legs:
                    continue
                leg_providers = {leg.get("provider") for leg in legs}
                if leg_providers.issubset(pool):
                    filtered.append(opp)
            opps = [o for o in filtered if o.get("guaranteed_profit_pct", 0) > 0]
            opps.sort(key=lambda o: o.get("guaranteed_profit_pct", 0), reverse=True)
            return opps
        except Exception:
            logger.exception(f"[Arb:{self.provider_id}] Failed to fetch arb opps")
            return []

    @staticmethod
    def _compute_slip_state(planned_odds: float, live_odds: float | None) -> str:
        """Per spec §4.2: green if live within drift tolerance of planned (or higher), else red."""
        if live_odds is None or live_odds <= 0:
            return "red"
        if live_odds < planned_odds * (1.0 - LEG_DRIFT_TOL_PCT):
            return "red"
        return "green"

    @staticmethod
    def _compute_opp_key(opp: dict, anchor_leg: dict) -> str:
        """Stable key for comparing two opps for dethrone (spec §4.2)."""
        return "|".join(
            [
                str(opp.get("event_id", "")),
                str(opp.get("market", "")),
                "" if opp.get("point") is None else str(opp.get("point")),
                str(anchor_leg.get("outcome", "")),
            ]
        )

    def _should_dethrone(self, top_opp: dict) -> bool:
        """Decide whether to swap to a new top opp (spec §4.2 hysteresis)."""
        legs = top_opp.get("arb_legs") or top_opp.get("legs", [])
        anchor_leg = next((l for l in legs if l.get("provider") == self.provider_id), None)
        if anchor_leg is None:
            return False
        new_key = self._compute_opp_key(top_opp, anchor_leg)
        if new_key == self.current_opp_key:
            return False
        new_profit = top_opp.get("guaranteed_profit_pct", 0.0)
        baseline = self._current_recomputed_profit_pct if self._current_recomputed_profit_pct is not None else 0.0
        return (new_profit - baseline) >= DETHRONE_HYSTERESIS_PCT

    async def _watch_top_opp(self) -> None:
        """Periodic re-rank loop. Cancelled when leaving STATE_STANDBY."""
        while True:
            try:
                await asyncio.sleep(RERANK_INTERVAL_S)
                opps = await self._fetch_arb_opps()
                if not opps:
                    continue
                top = opps[0]
                if self._should_dethrone(top):
                    self._broadcaster.publish(
                        "arb_dethroned",
                        {
                            "arb_group_id": self.current_arb_group_id,
                            "old_profit": self._current_recomputed_profit_pct,
                            "new_profit": top.get("guaranteed_profit_pct"),
                        },
                    )
                    self._dethroned_to = top
                    self._anchor_event.set()
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(f"[Arb:{self.provider_id}] top-opp watcher error")

    @staticmethod
    def _opp_to_bet(opp: dict, leg: dict) -> dict:
        """Convert an arb opp + leg into a bet dict compatible with ProviderRunner."""
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
            "provider_meta": dict(leg.get("provider_meta") or {}),  # From leg (matchup_id etc.)
        }

    async def _record_bet(self, bet: dict, result: PlacementResult, arb_group_id: str) -> None:
        """Record a bet to the server DB with arb group linkage."""
        from arnold.http_client import tunnel_client as _tc

        provider_bet_id = result.bet_id if isinstance(result.bet_id, str) and result.bet_id else None
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
            "provider_bet_id": provider_bet_id,
            "notes": f"arb_group:{arb_group_id}",
        }
        client = _tc()
        for attempt in range(3):
            try:
                resp = await client.post("/api/bets", json=payload, timeout=10.0)
                resp.raise_for_status()
                data = resp.json()
                logger.info(f"[Arb:{self.provider_id}] Recorded bet {data.get('bet_id', '?')} (group={arb_group_id})")
                return
            except Exception:
                logger.exception(f"[Arb:{self.provider_id}] Failed to record bet (attempt {attempt + 1}/3)")
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
        logger.error(f"[Arb:{self.provider_id}] Bet lost after 3 attempts: {payload}")

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
        """Reconcile DB against provider truth and broadcast bet_reconciled events."""
        from .reconcile import reconcile_and_publish

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
                logger.exception(f"[Arb:{pid}] sync_history failed")
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
            n = await reconcile_and_publish(
                self._proxy_url,
                _AUTH_HEADER,
                _AUTH_VALUE,
                pid,
                pending_bets,
                history,
                self._broadcaster,
                page=page,
                workflow=workflow,
            )
            if n:
                logger.info(f"[Arb:{pid}] reconciled {n} bets")

    async def _fetch_pending(self, provider_id: str) -> list[dict]:
        from arnold.http_client import tunnel_client as _tc

        try:
            resp = await _tc().get("/api/opportunities/play/pending-bets", timeout=30.0)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []
        for prov in data.get("providers", []):
            if prov.get("provider_id") == provider_id:
                return prov.get("bets", [])
        return []

    async def _fetch_placed_today(self, provider_id: str) -> None:
        from arnold.http_client import tunnel_client as _tc

        try:
            resp = await _tc().post("/api/opportunities/play/batch", json={}, timeout=30.0)
            resp.raise_for_status()
            data = resp.json()
            placed = data.get("placed_today", {})
            self._placed_today.update(placed)
        except Exception:
            logger.warning(f"[Arb:{provider_id}] failed to fetch placed_today")

    async def _post_balance(self, provider_id: str, balance: float) -> None:
        from arnold.http_client import tunnel_client as _tc

        try:
            resp = await _tc().post(f"/api/bankroll/set/{provider_id}", json={"balance": balance}, timeout=15.0)
            resp.raise_for_status()
        except Exception:
            pass
