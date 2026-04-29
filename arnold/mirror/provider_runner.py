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

from .slip_odds_stream import SlipOddsStream
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

# Dethrone-on-better: while a bet sits at READY, periodically check the cluster
# queue for a higher-edge bet. If a new bet's edge exceeds the current bet's by
# at least this many percentage points, auto-skip and let the runner pop the new
# top. Hysteresis prevents thrashing on small fluctuations — but too high and
# we sit on a stale bet while a better one ages out. 2pts is aggressive enough
# to switch when the queue clearly has a winner, conservative enough to ignore
# noise. Mirrors arb_runner.py:DETHRONE_HYSTERESIS_PCT pattern.
DETHRONE_HYSTERESIS_PCT = 2.0
DETHRONE_POLL_S = 3.0

# Edge-drift skip: DISABLED. The previous threshold (5pts of edge dropped from
# queue cache to live) was too aggressive — it killed FRESH top-edge bets the
# moment polymarket tightened a few cents. Skip semantics now rely on:
#   (1) absolute edge < 0 (negative EV) — always skip, in slip-stream callback
#   (2) dethrone hysteresis — switch to better bet in queue if it appears
#   (3) READY_TIMEOUT_S — eventually cycle off bets the user hasn't acted on
# Keeping the constant for back-compat / future tuning but set to 0 (off).
EDGE_DRIFT_SKIP_PCT = 0.0

# READY-state timeout: max seconds to sit at READY waiting for user to click
# Buy on the provider's tab. After this, auto-skip and pop the next bet.
# Long enough to let user evaluate and click without thrashing. The runner
# will return to a previously-skipped bet on the next refresh cycle if it's
# still top-edge. Set 0 to disable (wait forever, original behavior).
READY_TIMEOUT_S = 120.0


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
        peek_top_edge: Callable[[], float | None] | None = None,
        stake_caps: dict[str, float] | None = None,
        mark_recently_skipped: Callable[[dict], None] | None = None,
    ):
        self.provider_id = provider_id
        self._browser = browser
        self._broadcaster = broadcaster
        self._proxy_url = proxy_url.rstrip("/")
        self._pop_bet = pop_bet
        self._block_event_market = block_event_market
        self._is_blocked = is_blocked
        self._placed_today = placed_today
        self._peek_top_edge = peek_top_edge
        self._stake_caps = stake_caps if stake_caps is not None else {}
        self._mark_recently_skipped = mark_recently_skipped or (lambda _b: None)

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
        self._slip_stream = None  # Set when a slip is loaded; cleared when bet ready/placed/skipped

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name=f"runner_{self.provider_id}")

    def stop(self) -> None:
        if self._slip_stream is not None:
            try:
                self._slip_stream.stop()
            except Exception:
                pass
            self._slip_stream = None
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
                    # Queue empty — but don't exit. Coordinator's _refresh_batch
                    # adds new opportunities every 10s. Idle-wait and retry so a
                    # fresh +EV opp picks up the runner without having to restart
                    # the whole play loop. Cap the idle wait so we exit cleanly
                    # if the user truly stops play.
                    self._broadcaster.publish(
                        "queue_idle",
                        {"provider_id": pid, "msg": "waiting for new opportunities"},
                    )
                    idle_seconds = 0
                    while idle_seconds < 600:  # max 10 min idle, then exit
                        await asyncio.sleep(5)
                        idle_seconds += 5
                        if self._peek_top_edge and self._peek_top_edge() is not None:
                            break
                    bet = self._pop_bet()
                    if bet is None:
                        logger.info(f"[Runner:{pid}] Queue still empty after 10min idle — done")
                        break
                    logger.info(f"[Runner:{pid}] Resumed from idle — {idle_seconds}s wait")

                # Release the tab back to home_url so the pending loop can sync
                # history while we wait for the next bet to be popped/processed.
                # The next iteration's navigate_to_event will move it back.
                # Only do this between bets (not on the very first bet).
                if self.stats["total"] > 0:
                    try:
                        page_release = await workflow.find_tab(self._browser.context) if self._browser.context else None
                        if page_release and workflow.home_url and workflow.domain not in (page_release.url or ""):
                            pass  # Already away from provider — let the user / pending_loop drive
                        elif page_release and workflow.home_url:
                            current = (page_release.url or "").rstrip("/")
                            home = workflow.home_url.rstrip("/")
                            if current != home:
                                await page_release.goto(workflow.home_url, wait_until="domcontentloaded", timeout=10000)
                    except Exception:
                        pass

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
                # Apply per-provider stake cap (learned from prior limit responses)
                cap = self._stake_caps.get(pid)
                if cap is not None and cap > 0 and stake > cap:
                    logger.info(f"[Runner:{pid}] Capping stake {stake} → {cap} (provider limit)")
                    stake = cap
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

                # Stream slip odds while waiting for placement/skip. Broadcasts
                # live_price on every meaningful change and auto-skips on
                # negative EV or edge collapse without inline polling.
                _last_live_odds = live_odds
                _last_live_edge = live_edge
                _auto_skip_reason: str | None = None

                def _on_slip_change(odds: float) -> None:
                    nonlocal _last_live_odds, _last_live_edge, _auto_skip_reason
                    fair = bet.get("fair_odds")
                    edge = ((odds / fair) - 1) * 100 if fair else None
                    _last_live_odds = odds
                    _last_live_edge = edge
                    self._broadcaster.publish(
                        "live_price",
                        {
                            "event_id": bet.get("event_id", ""),
                            "market": bet.get("market", ""),
                            "outcome": bet.get("outcome", ""),
                            "provider_id": pid,
                            "live_odds": odds,
                            "live_edge": edge,
                            "fair_odds": fair,
                        },
                    )
                    # Auto-skip logic
                    if edge is not None:
                        if edge < 0:
                            _auto_skip_reason = f"negative EV ({odds:.2f}, edge {edge:.1f}%)"
                            self._skip_event.set()
                            return
                        # Edge-drift from intent: live odds moved against us so
                        # the realised edge dropped meaningfully from what we
                        # queued at. Disabled when EDGE_DRIFT_SKIP_PCT <= 0 —
                        # the > 0 guard prevents skipping on every tiny positive
                        # drift (since intent - live >= 0 is true any time live
                        # edge has tightened at all, even by 0.1pts).
                        intent_edge = bet.get("edge_pct")
                        if (
                            EDGE_DRIFT_SKIP_PCT > 0
                            and intent_edge is not None
                            and (intent_edge - edge) >= EDGE_DRIFT_SKIP_PCT
                        ):
                            _auto_skip_reason = (
                                f"edge drift {edge:.1f}% (intent {intent_edge:.1f}%, "
                                f"lost {intent_edge - edge:.1f}pts ≥ {EDGE_DRIFT_SKIP_PCT:.0f})"
                            )
                            self._skip_event.set()
                            return
                        if self._peek_top_edge:
                            top_edge = self._peek_top_edge()
                            if top_edge is not None and top_edge > 0 and edge < top_edge * 0.5:
                                _auto_skip_reason = f"edge dropped ({edge:.1f}% < 50% of top {top_edge:.1f}%)"
                                self._skip_event.set()

                # Dethrone watcher: while we're at READY, periodically check the
                # cluster queue. If a NEW bet beats THIS bet's LIVE edge by
                # >= hysteresis, auto-skip so the runner pops the new top.
                # Uses live_edge_holder[0] (the polymarket watcher updates this
                # every 1s with the freshest live edge) so we demote a bet
                # whose live odds tightened below the queue's cached top.
                # Falls back to cached batch edge until first live read lands.
                _dethrone_reason: str | None = None
                _intent_edge = bet.get("edge_pct") or 0.0
                # Mutable single-element list so closures in both watchers can
                # share state without nonlocal gymnastics across nested scopes.
                live_edge_holder: list[float | None] = [None]

                # Active bet's queue key — passed to peek_top so it excludes
                # the active bet's own re-added entry when computing the queue's
                # top edge. Without this exclusion, refresh_batch re-adds the
                # active bet at its cached edge and dethrone fires falsely
                # whenever the live edge dips below the cached value.
                _active_key = (bet.get("event_id"), bet.get("market"), bet.get("outcome"))

                async def _watch_for_better() -> None:
                    nonlocal _dethrone_reason, _auto_skip_reason
                    while True:
                        try:
                            await asyncio.sleep(DETHRONE_POLL_S)
                        except asyncio.CancelledError:
                            raise
                        if self._peek_top_edge is None:
                            continue
                        try:
                            top_edge = self._peek_top_edge(_active_key)
                        except TypeError:
                            # Older peek_top callable without exclude_key kwarg
                            top_edge = self._peek_top_edge()
                        except Exception:
                            continue
                        if top_edge is None:
                            continue
                        # Compare against LIVE edge (preferred) or cached intent
                        # (fallback before first live read).
                        compare_edge = live_edge_holder[0]
                        if compare_edge is None:
                            compare_edge = _intent_edge
                        if top_edge >= compare_edge + DETHRONE_HYSTERESIS_PCT:
                            _dethrone_reason = (
                                f"dethroned by +{top_edge:.1f}% bet "
                                f"(current live +{compare_edge:.1f}%, hysteresis {DETHRONE_HYSTERESIS_PCT:.1f}pts)"
                            )
                            _auto_skip_reason = _dethrone_reason
                            self._broadcaster.publish(
                                "bet_dethroned",
                                {
                                    "bet": bet,
                                    "provider_id": pid,
                                    "old_edge": compare_edge,
                                    "new_top_edge": top_edge,
                                },
                            )
                            self._skip_event.set()
                            return

                self._slip_stream = SlipOddsStream(
                    provider_id=pid,
                    workflow=workflow,
                    page=page,
                    on_odds_change=_on_slip_change,
                    poll_interval_s=1.0,
                )
                self._slip_stream.start()
                _dethrone_task = asyncio.create_task(_watch_for_better(), name=f"dethrone_{pid}")

                # Polymarket-specific watcher: SlipOddsStream's read_slip_odds is
                # not implemented for polymarket (no betslip-widget scraper like
                # altenar), so the stream's _on_slip_change never fires for poly.
                # That breaks BOTH (a) auto-skip on edge < 0 and (b) live edge
                # display in the UI. Plus polymarket's React occasionally clobbers
                # the Amount input after prep, so we need a continuous re-fill.
                # This watcher polls workflow.check_live_price (which IS wired)
                # every 1s and reuses the existing _on_slip_change callback path.
                _poly_watch_task: asyncio.Task | None = None

                async def _watch_polymarket() -> None:
                    # Two responsibilities, every 1s:
                    #   (a) Amount-keeper: re-fill betslip if React clobbered it.
                    #   (b) Live edge poll: read cents → compute edge → publish
                    #       to live_edge_holder[0] so the dethrone watcher can
                    #       compare against the FRESHEST live edge (not the
                    #       stale cached batch value). Also broadcast live_price
                    #       SSE for the UI's table colors.
                    # Crucially does NOT fire _skip_event on its own — earlier
                    # version did via _on_slip_change and was firing spurious
                    # skips. Skipping is now ONLY the dethrone watcher's job
                    # (live vs queue comparison) or user Skip click.
                    bet_ns = _bet_ns(bet)
                    fair = bet.get("fair_odds")
                    strat = getattr(workflow, "strategy", None)
                    restore = getattr(strat, "restore_amount_if_cleared", None) if strat else None
                    while True:
                        try:
                            await asyncio.sleep(1.0)
                        except asyncio.CancelledError:
                            raise
                        # Live edge read
                        try:
                            result = await workflow.check_live_price(page, bet_ns)
                        except Exception:
                            result = None
                        if isinstance(result, tuple) and len(result) == 2:
                            live_o, live_e = result
                            if live_e is not None:
                                live_edge_holder[0] = live_e
                                self._broadcaster.publish(
                                    "live_price",
                                    {
                                        "event_id": bet.get("event_id", ""),
                                        "market": bet.get("market", ""),
                                        "outcome": bet.get("outcome", ""),
                                        "provider_id": pid,
                                        "live_odds": live_o,
                                        "live_edge": live_e,
                                        "fair_odds": fair,
                                    },
                                )
                        # Amount-keeper
                        if restore:
                            try:
                                await restore(page, stake)
                            except Exception:
                                logger.debug(f"[Runner:{pid}] amount-keeper raised", exc_info=True)

                if pid == "polymarket":
                    _poly_watch_task = asyncio.create_task(_watch_polymarket(), name=f"poly_watch_{pid}")

                try:
                    # READY-state timeout: cycle to next bet if user hasn't
                    # clicked within READY_TIMEOUT_S. Without this we camp on
                    # the first +EV bet forever, which blocks the runner from
                    # showing the user other options that may be just as good
                    # or better.
                    _wait_timeout = READY_TIMEOUT_S if READY_TIMEOUT_S > 0 else None
                    done, pending = await asyncio.wait(
                        [
                            asyncio.ensure_future(self._bet_intercepted_event.wait()),
                            asyncio.ensure_future(self._skip_event.wait()),
                        ],
                        return_when=asyncio.FIRST_COMPLETED,
                        timeout=_wait_timeout,
                    )
                    # Cancel any still-pending awaits to release resources.
                    for fut in pending:
                        fut.cancel()
                    if not done and _wait_timeout:
                        # Timeout fired — auto-skip on stale READY.
                        _auto_skip_reason = f"READY-timeout ({READY_TIMEOUT_S:.0f}s without user click)"
                        self._skip_event.set()
                finally:
                    if _poly_watch_task and not _poly_watch_task.done():
                        _poly_watch_task.cancel()
                        try:
                            await _poly_watch_task
                        except (asyncio.CancelledError, Exception):
                            pass
                    if not _dethrone_task.done():
                        _dethrone_task.cancel()
                        try:
                            await _dethrone_task
                        except (asyncio.CancelledError, Exception):
                            pass
                    if self._slip_stream is not None:
                        self._slip_stream.stop()
                        self._slip_stream = None
                live_odds = _last_live_odds
                live_edge = _last_live_edge

                # If auto-skip fired during streaming, broadcast bet_skipped here
                # (the wait above completed because _skip_event was set)
                if _auto_skip_reason is not None and not self._bet_intercepted_event.is_set():
                    logger.info(f"[Runner:{pid}] Auto-skip: {_auto_skip_reason}")
                    self._broadcaster.publish(
                        "bet_skipped",
                        {
                            "bet": bet,
                            "reason": _auto_skip_reason,
                            "live_odds": live_odds,
                            "live_edge": live_edge,
                        },
                    )
                    self.stats["skipped"] += 1
                    # Auto-skip path (dethrone / READY-timeout / edge<0). Mark
                    # so refresh_batch doesn't immediately re-add the bet to
                    # queue and trigger the same cascade again.
                    self._mark_recently_skipped(bet)

                if self._bet_intercepted_event.is_set():
                    self.state = STATE_PLACING
                    try:
                        await self._handle_placement(bet, pid, workflow, page, prep_result, stake)
                    except Exception:
                        logger.exception(f"[Runner:{pid}] Recording failed")
                        self._broadcaster.publish("bet_error", {"bet": bet, "reason": "record_exception"})
                        self.stats["skipped"] += 1
                        self._mark_recently_skipped(bet)
                elif self._skip_event.is_set() and _auto_skip_reason is None:
                    self._broadcaster.publish("bet_skipped", {"bet": bet, "reason": "user_skip"})
                    self.stats["skipped"] += 1
                    # User skip — mark so refresh doesn't re-queue the same bet.
                    self._mark_recently_skipped(bet)
                # else: auto-skipped by stream callback (already broadcast + counted)

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
                # Save cap for this provider so future bets respect it
                prev_cap = self._stake_caps.get(pid)
                if prev_cap is None or actual_stake < prev_cap:
                    self._stake_caps[pid] = actual_stake
                    logger.info(f"[Runner:{pid}] Stake cap learned: {actual_stake} (was requesting {requested_stake})")
                self._broadcaster.publish(
                    "stake_limited",
                    {
                        "bet": bet,
                        "provider_id": pid,
                        "requested_stake": requested_stake,
                        "actual_stake": actual_stake,
                        "cap": self._stake_caps[pid],
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

        # Polymarket (or any provider with scrape_portfolio+claim_banner+redeem_all
        # in its Strategy): DOM-based positions scrape → claim/redeem → match pending.
        strat = getattr(workflow, "strategy", None)
        intel = getattr(workflow, "intel", None)
        supports_claim_redeem = bool(
            strat
            and getattr(strat, "claim_banner", None)
            and getattr(strat, "redeem_all", None)
            and getattr(strat, "scrape_portfolio", None)
        )
        if supports_claim_redeem:
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
                positions = await strat.scrape_portfolio(page, intel)
                logger.info(f"[Runner:{provider_id}] Scraped {len(positions)} positions")

                # Match scraped positions against pending bets to build settlements
                settlements = []
                if pending_bets and positions:
                    settlements = self._match_polymarket_settlements(pending_bets, positions)
                    logger.info(f"[Runner:{provider_id}] Matched {len(settlements)} settlements")

                # Click Claim banner if present
                claim_result = await strat.claim_banner(page, intel)
                if claim_result.get("claimed"):
                    logger.info(f"[Runner:{provider_id}] Claimed: {claim_result.get('amount')}")
                    await asyncio.sleep(2)

                # Click Redeem buttons for finished positions
                redeem_result = await strat.redeem_all(page, intel)
                logger.info(f"[Runner:{provider_id}] Redeem: {redeem_result}")

                # Record settlements to DB via API proxy
                if settlements:
                    await self._record_settlements(provider_id, settlements)
                    logger.info(f"[Runner:{provider_id}] Recorded {len(settlements)} settlements to DB")
                    self._broadcaster.publish(
                        "settlements_confirmed",
                        {"provider_id": provider_id, "settlements": settlements},
                    )

                # History fallback: any DB pending bet not represented in current
                # positions is either already-redeemed (out of positions view) or a
                # ghost. Scrape history → fuzzy-match → reconcile → settle. Without
                # this, old already-resolved bets accumulate as ghost-pending forever.
                settled_ids = {s.get("bet_id") for s in settlements if s.get("bet_id")}
                unmatched = [b for b in pending_bets if b.get("bet_id") not in settled_ids]
                if unmatched and getattr(strat, "sync_history", None):
                    try:
                        raw_history = await workflow.sync_history(page)
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
                        from .reconcile import reconcile_and_publish

                        n = await reconcile_and_publish(
                            self._proxy_url,
                            _AUTH_HEADER,
                            _AUTH_VALUE,
                            provider_id,
                            unmatched,
                            history,
                            self._broadcaster,
                            page=page,
                            workflow=workflow,
                        )
                        if n:
                            logger.info(
                                f"[Runner:{provider_id}] history-fallback reconciled {n}/{len(unmatched)} unmatched bets"
                            )
                    except Exception:
                        logger.exception(f"[Runner:{provider_id}] history-fallback reconcile failed")

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

        # Reconcile DB against provider truth (autonomous — DB self-heals)
        if pending_bets:
            from .reconcile import reconcile_and_publish

            n = await reconcile_and_publish(
                self._proxy_url,
                _AUTH_HEADER,
                _AUTH_VALUE,
                provider_id,
                pending_bets,
                history,
                self._broadcaster,
                page=page,
                workflow=workflow,
            )
            if n:
                logger.info(f"[Runner:{provider_id}] reconciled {n} bets")
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
                "provider_bet_id": entry.get("provider_bet_id") or None,
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
        provider_bet_id = result.bet_id if isinstance(result.bet_id, str) and result.bet_id else None
        # Capture analytics-critical fields from the queued bet so post-settlement
        # CLV / edge / kelly drift can be computed against the placement-time fair odds.
        # Without these, settled bets carry actual_odds + result only — no way to
        # back out whether the model's edge prediction held up.
        fair_odds = bet.get("fair_odds")
        edge_pct = bet.get("edge_pct")
        # selection_probability is the implied true win-rate (1 / fair_odds) — used
        # for Brier-score / calibration analysis. Skip if fair_odds missing or zero.
        selection_prob = (1.0 / fair_odds) if fair_odds and fair_odds > 0 else None
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
            "fair_odds_at_placement": fair_odds,
            "selection_probability": selection_prob,
            "utility_score": edge_pct,
            "bet_type": bet.get("bet_type") or bet.get("tier") or "value",
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
