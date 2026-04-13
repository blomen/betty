"""PlayLoop — automated betting state machine.

Iterates a sorted queue of bets, handles provider tab management,
login waiting, pending settlement scan, navigation, and user-driven
place/skip decisions.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .browser import MirrorBrowser
from .pending_loop import _detect_settlements  # noqa: F401
from .sse import MirrorBroadcaster
from .workflows import get_workflow
from .workflows.base import PlacementResult

logger = logging.getLogger(__name__)

# Cluster membership — same odds across all siblings
_CLUSTER_MEMBERS: dict[str, list[str]] = {
    "kambi": ["unibet", "leovegas", "expekt", "betmgm", "speedybet", "x3000", "goldenbull", "1x2"],
    "spectate": ["888sport", "mrgreen"],
    "altenar_main": ["betinia", "campobet", "lodur", "quickcasino", "swiper", "dbet"],
    "gecko_betsson": ["betsson", "nordicbet", "betsafe", "spelklubben"],
    "comeon_group": ["comeon", "lyllo", "hajper", "snabbare"],
}
_PROVIDER_TO_CLUSTER: dict[str, str] = {}
for _cname, _members in _CLUSTER_MEMBERS.items():
    for _m in _members:
        _PROVIDER_TO_CLUSTER[_m] = _cname

_AUTH_HEADER = "X-Nginx-Authenticated"
_AUTH_VALUE = "firevsports"

# State constants
STATE_IDLE = "idle"
STATE_RUNNING = "running"
STATE_PROVIDER_OPENING = "provider_opening"
STATE_LOGIN_WAITING = "login_waiting"
STATE_SETTLING = "settling"
STATE_NAVIGATING = "navigating"
STATE_READY = "ready"
STATE_PLACING = "placing"

LOGIN_POLL_INTERVAL = 5.0  # seconds between login checks
LOGIN_TIMEOUT = 120.0  # seconds to wait for login before skipping provider
DAILY_BET_CAP = 10  # max bets per soft provider per day
UNCAPPED_PROVIDERS = {"pinnacle", "polymarket", "cloudbet"}


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
        self._placed_today: dict[str, int] = {}  # provider_id → bets placed today (from server)
        self._blocked: set[tuple[str, str]] = set()  # (event_id, market) — placed, block across all providers
        self._confirmed_settlements: list[dict] | None = None  # set by confirm_settlements()

        # Queue
        self._queue: list[dict] = []
        self._queue_total: int = 0

        # Async control
        self._task: asyncio.Task | None = None
        self._place_event: asyncio.Event = asyncio.Event()
        self._skip_event: asyncio.Event = asyncio.Event()
        self._settle_confirm_event: asyncio.Event = asyncio.Event()
        self._bet_intercepted_event: asyncio.Event = asyncio.Event()
        self._intercepted_body: dict | None = None  # placeWidget response body

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_batch(self, batch: list[dict], balances: dict[str, float], start_provider: str | None = None) -> None:
        """Filter to funded clusters, sort by edge desc.

        All bets in a cluster are interchangeable — if any sibling is funded,
        all cluster bets are eligible. The active skin handles placement.
        """
        # Expand funded set to include all cluster siblings
        funded_clusters: set[str] = set()
        for pid, bal in balances.items():
            if bal > 0:
                cluster = _PROVIDER_TO_CLUSTER.get(pid)
                if cluster:
                    funded_clusters.add(cluster)
                funded_clusters.add(pid)  # standalone providers

        def _is_funded(b: dict) -> bool:
            pid = b.get("provider_id", "")
            cluster = _PROVIDER_TO_CLUSTER.get(pid)
            return pid in funded_clusters or (cluster is not None and cluster in funded_clusters)

        filtered = [b for b in batch if _is_funded(b)]
        # Sort purely by edge — all cluster bets are equivalent
        filtered.sort(key=lambda b: -b.get("edge_pct", 0.0))
        self._queue = filtered
        self._queue_total = len(filtered)
        self._start_provider = start_provider
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
        self._place_event.set()  # unblock any waits
        self._skip_event.set()
        self._settle_confirm_event.set()
        self._bet_intercepted_event.set()

    def place(self) -> None:
        """Signal that the user wants to place the current bet."""
        self._skip_event.clear()
        self._place_event.set()

    def skip(self) -> None:
        """Signal that the user wants to skip the current bet."""
        self._place_event.clear()
        self._skip_event.set()

    def on_bet_intercepted(self, provider_id: str, body: dict) -> None:
        """Called by browser interceptor when a placeWidget response is detected.

        If the play loop is in READY state for this provider, auto-records
        the bet and advances to the next one.
        """
        if self.state != STATE_READY:
            return
        bet = self.current_bet
        if not bet or bet.get("provider_id") != provider_id:
            return
        logger.info(f"[PlayLoop] Bet intercepted for {provider_id} — auto-recording")
        self._intercepted_body = body
        self._bet_intercepted_event.set()

    def confirm_settlements(self, confirmed: list[dict] | None = None) -> None:
        """Signal that the user has confirmed the settlement breakdown.

        If confirmed is provided, only those settlements will be recorded.
        """
        self._confirmed_settlements = confirmed
        self._settle_confirm_event.set()

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
            # Settle pending bets for the start provider before processing queue
            if self._start_provider and self._browser.context:
                pid = self._start_provider
                workflow = get_workflow(pid)
                page = await workflow.find_tab(self._browser.context)
                if page is None:
                    for p in self._browser.context.pages:
                        if workflow.domain and workflow.domain in p.url:
                            page = p
                            break
                if page:
                    self.state = STATE_LOGIN_WAITING
                    self._broadcaster.publish("login_waiting", {"provider_id": pid})
                    logged_in = await self._wait_for_login(workflow, page)
                    if logged_in:
                        current_provider = pid
                        await self._settle_pending(pid, workflow, page)
                        if pid not in UNCAPPED_PROVIDERS:
                            await self._fetch_placed_today(pid)

            while self._queue:
                bet = self._queue.pop(0)
                provider_id: str = bet.get("provider_id", "")

                # Cluster-aware: treat all siblings as the same provider.
                # Override bet's provider_id to the active skin so navigation
                # uses the correct domain and tab.
                bet_cluster = _PROVIDER_TO_CLUSTER.get(provider_id)
                start_cluster = _PROVIDER_TO_CLUSTER.get(self._start_provider or "")
                current_cluster = _PROVIDER_TO_CLUSTER.get(current_provider or "")

                # If current_provider not yet set but bet is in same cluster
                # as the start provider, adopt start_provider as current
                if current_provider is None and bet_cluster and bet_cluster == start_cluster:
                    provider_id = self._start_provider
                    bet["provider_id"] = provider_id
                elif bet_cluster and current_cluster and bet_cluster == current_cluster:
                    provider_id = current_provider
                    bet["provider_id"] = provider_id

                # Skip if this event+market was already placed on another provider
                m = bet.get("market", "")
                m_key = "moneyline" if m in ("1x2", "moneyline") else m
                if (bet.get("event_id", ""), m_key) in self._blocked:
                    continue

                # Init stats for this provider
                if provider_id not in self.provider_stats:
                    self.provider_stats[provider_id] = {"placed": 0, "skipped": 0, "total": 0}
                self.provider_stats[provider_id]["total"] += 1

                # Provider change — stop and let user pick next skin
                if provider_id != current_provider:
                    if current_provider is not None:
                        self._queue.insert(0, bet)
                        self._broadcaster.publish("provider_complete", {"provider_id": current_provider})
                        break

                    current_provider = provider_id
                    workflow = get_workflow(provider_id)

                    # Find existing tab or open new one
                    self.state = STATE_PROVIDER_OPENING
                    self._broadcaster.publish("provider_opening", {"provider_id": provider_id})
                    await asyncio.sleep(1)
                    page = await workflow.find_tab(self._browser.context) if self._browser.context else None

                    if page is None and self._browser.context:
                        domain = workflow.domain
                        for p in self._browser.context.pages:
                            if domain and domain in p.url:
                                page = p
                                break

                    if page is None:
                        domain = workflow.domain
                        url = workflow.home_url if domain else None
                        if url and self._browser.context:
                            logger.info(f"[PlayLoop] Opening tab for {provider_id}: {url}")
                            page = await self._browser.open_tab(url)
                        else:
                            logger.warning(
                                f"[PlayLoop] No domain for {provider_id}, cannot open tab — skipping provider"
                            )
                            self._skip_provider(provider_id)
                            current_provider = None
                            continue

                    # Wait for login
                    self.state = STATE_LOGIN_WAITING
                    self._broadcaster.publish("login_waiting", {"provider_id": provider_id})
                    logged_in = await self._wait_for_login(workflow, page)

                    if not logged_in:
                        logger.warning(f"[PlayLoop] Login timeout for {provider_id} — skipping provider")
                        self._broadcaster.publish(
                            "provider_skipped",
                            {
                                "provider_id": provider_id,
                                "reason": "login_timeout",
                            },
                        )
                        self._skip_provider(provider_id)
                        current_provider = None
                        continue

                    # Scan pending bets for settlements before placing new bets
                    await self._settle_pending(provider_id, workflow, page)

                    # Fetch placed-today count and check daily cap (soft only)
                    if provider_id not in UNCAPPED_PROVIDERS:
                        await self._fetch_placed_today(provider_id)
                        placed = self._placed_today.get(provider_id, 0)
                        if placed >= DAILY_BET_CAP:
                            logger.info(f"[PlayLoop] {provider_id} at daily cap ({placed}/{DAILY_BET_CAP})")
                            self._broadcaster.publish(
                                "provider_complete",
                                {"provider_id": provider_id, "reason": f"daily cap ({placed}/{DAILY_BET_CAP})"},
                            )
                            current_provider = None
                            continue

                # Check daily cap before each bet (soft only)
                if provider_id not in UNCAPPED_PROVIDERS:
                    placed = self._placed_today.get(provider_id, 0)
                    if placed >= DAILY_BET_CAP:
                        logger.info(f"[PlayLoop] {provider_id} hit daily cap mid-session")
                        self._broadcaster.publish(
                            "provider_complete",
                            {"provider_id": provider_id, "reason": f"daily cap ({placed}/{DAILY_BET_CAP})"},
                        )
                        break

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

                # Auto-fill betslip (outcome + stake) before showing confirm
                stake = bet.get("stake", 0.0)
                prep_result = await workflow.prep_betslip(page, bet, stake)
                prep_ok = prep_result.status == "prepped"

                # Ready — wait for bet interception (user places manually) or skip
                self.state = STATE_READY
                self._place_event.clear()
                self._skip_event.clear()
                self._bet_intercepted_event.clear()
                self._intercepted_body = None
                self._broadcaster.publish(
                    "bet_ready",
                    {
                        "bet": bet,
                        "provider_id": provider_id,
                        "prep_ok": prep_ok,
                        "live_odds": prep_result.actual_odds,
                        "prep_reason": prep_result.reason,
                    },
                )

                done, _ = await asyncio.wait(
                    [
                        asyncio.ensure_future(self._bet_intercepted_event.wait()),
                        asyncio.ensure_future(self._skip_event.wait()),
                        asyncio.ensure_future(self._place_event.wait()),
                    ],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if self._bet_intercepted_event.is_set() or self._place_event.is_set():
                    # Bet placed — either intercepted from browser or confirmed via app
                    self.state = STATE_PLACING
                    try:
                        # Extract bet ID + actual stake/odds from intercepted response
                        provider_bet_id = None
                        actual_odds = prep_result.actual_odds
                        actual_stake = prep_result.actual_stake
                        if self._intercepted_body:
                            provider_bet_id = workflow.parse_placement_response(self._intercepted_body)
                            # Parse actual stake/odds — provider may limit stake
                            if hasattr(workflow, "parse_placement_details"):
                                details = workflow.parse_placement_details(self._intercepted_body)
                                if details.get("actual_stake"):
                                    actual_stake = details["actual_stake"]
                                if details.get("actual_odds"):
                                    actual_odds = details["actual_odds"]
                            logger.info(
                                f"[PlayLoop] Intercepted bet_id={provider_bet_id} "
                                f"stake={actual_stake} odds={actual_odds}"
                            )

                        result = PlacementResult(
                            status="placed",
                            bet_id=provider_bet_id or 0,
                            actual_odds=actual_odds,
                            actual_stake=actual_stake,
                            reason="intercepted" if self._intercepted_body else "manual",
                        )
                        placed_count = self._placed_today.get(provider_id, 0) + 1
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
                        self.provider_stats[provider_id]["placed"] += 1
                        self._placed_today[provider_id] = self._placed_today.get(provider_id, 0) + 1
                        await self._record_bet(bet, result)
                        self._block_event_market(bet)
                        # Sync balance from interceptor cache
                        cached_bal = self._browser.provider_data.get(provider_id, {}).get("balance")
                        if cached_bal is not None:
                            await self._post_balance(provider_id, cached_bal)
                    except Exception:
                        logger.exception(f"[PlayLoop] Recording failed for {provider_id}")
                        self._broadcaster.publish("bet_error", {"bet": bet, "reason": "record_exception"})
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
        """Wait for login by checking intercepted balance data from browser."""
        await asyncio.sleep(2)
        elapsed = 2.0
        while elapsed < LOGIN_TIMEOUT:
            # Check intercepted data first (set by browser's response listener)
            if self._browser.is_logged_in(workflow.provider_id):
                bal = self._browser.get_balance(workflow.provider_id)
                self._broadcaster.publish(
                    "login_detected",
                    {
                        "provider_id": workflow.provider_id,
                        "balance": bal,
                    },
                )
                logger.info(f"[PlayLoop] Login detected for {workflow.provider_id} (balance: {bal})")
                return True
            # Fallback: check DOM for balance text
            try:
                dom_result = await self._browser.check_login_dom(workflow.provider_id)
                if dom_result.get("logged_in"):
                    self._broadcaster.publish(
                        "login_detected",
                        {
                            "provider_id": workflow.provider_id,
                            "balance": dom_result.get("balance"),
                        },
                    )
                    logger.info(
                        f"[PlayLoop] Login detected for {workflow.provider_id} (via DOM: {dom_result.get('balance')})"
                    )
                    return True
            except Exception:
                pass
            await asyncio.sleep(LOGIN_POLL_INTERVAL)
            elapsed += LOGIN_POLL_INTERVAL
            self._broadcaster.publish(
                "login_waiting",
                {
                    "provider_id": workflow.provider_id,
                    "elapsed": round(elapsed),
                    "timeout": LOGIN_TIMEOUT,
                },
            )
        return False

    def _skip_provider(self, provider_id: str) -> None:
        """Mark remaining bets for this provider as skipped and drain them from the queue."""
        remaining = [b for b in self._queue if b.get("provider_id") == provider_id]
        for b in remaining:
            self._queue.remove(b)
            self.provider_stats.setdefault(provider_id, {"placed": 0, "skipped": 0, "total": 0})
            self.provider_stats[provider_id]["skipped"] += 1
            self.provider_stats[provider_id]["total"] += 1

    def _block_event_market(self, bet: dict) -> None:
        """After placing a bet, block the same event+market across all providers."""
        event_id = bet.get("event_id", "")
        market = bet.get("market", "")
        # Normalize: 1x2 and moneyline are the same market type for blocking
        market_key = "moneyline" if market in ("1x2", "moneyline") else market
        block_key = (event_id, market_key)
        self._blocked.add(block_key)
        # Remove matching bets from queue
        before = len(self._queue)
        self._queue = [
            b
            for b in self._queue
            if (b.get("event_id"), "moneyline" if b.get("market") in ("1x2", "moneyline") else b.get("market"))
            != block_key
        ]
        removed = before - len(self._queue)
        if removed:
            logger.info(f"[PlayLoop] Blocked {event_id} {market_key} — removed {removed} bets from queue")

    async def _settle_pending(self, provider_id: str, workflow, page) -> None:
        """Scan pending bets, show breakdown, wait for user confirm, then proceed."""
        self.state = STATE_SETTLING
        self._broadcaster.publish("settling_pending", {"provider_id": provider_id})

        # Reconcile: check open bets on provider vs our DB
        await self._reconcile_open_bets(provider_id, workflow, page)

        # Fetch pending bets for this provider from server
        pending_bets = await self._fetch_pending(provider_id)
        if not pending_bets:
            logger.info(f"[PlayLoop] No pending bets for {provider_id}")
            self._broadcaster.publish(
                "settling_done",
                {
                    "provider_id": provider_id,
                    "pending_count": 0,
                    "settlements": [],
                },
            )
            return

        # Compare provider's open bets vs our pending count.
        # If provider has fewer open bets, some settled → need to sync.
        # If equal or more, skip settlement sync entirely.
        try:
            positions = await workflow.fetch_positions(page) if hasattr(workflow, "fetch_positions") else None
        except Exception:
            positions = None

        if positions is not None:
            provider_open = len(positions)
            our_pending = len(pending_bets)
            if provider_open >= our_pending:
                logger.info(
                    f"[PlayLoop] Provider has {provider_open} open, we have {our_pending} pending "
                    f"— no settlements, skipping sync"
                )
                self._broadcaster.publish(
                    "settling_done",
                    {
                        "provider_id": provider_id,
                        "pending_count": our_pending,
                        "settlements": [],
                    },
                )
                return
            logger.info(
                f"[PlayLoop] Provider has {provider_open} open vs {our_pending} pending "
                f"— {our_pending - provider_open} likely settled, syncing"
            )

        # Use stream cache if available and fresh, otherwise fetch from provider
        from . import stream_registry

        stream = stream_registry.get(provider_id)
        if stream and stream.is_history_fresh():
            raw_history = stream.get_history()
            logger.info(f"[PlayLoop] Using stream cache for {provider_id} ({len(raw_history)} entries)")
        else:
            try:
                raw_history = await workflow.sync_history(page)
            except Exception:
                logger.exception(f"[PlayLoop] sync_history failed for {provider_id}")
                self._broadcaster.publish(
                    "settling_done",
                    {
                        "provider_id": provider_id,
                        "pending_count": len(pending_bets),
                        "settlements": [],
                    },
                )
                return

        history = [
            {
                "odds": e.odds,
                "stake": e.stake,
                "status": e.status,
                "payout": e.payout,
                "provider_bet_id": e.provider_bet_id,
                "event_name": e.event_name,
            }
            for e in raw_history
        ]

        # Detect settlements
        settlements = _detect_settlements(pending_bets, history)

        # Broadcast full breakdown — pending bets + any detected settlements
        self._broadcaster.publish(
            "settling_done",
            {
                "provider_id": provider_id,
                "pending_count": len(pending_bets),
                "pending_bets": pending_bets,
                "settlements": settlements,
            },
        )

        if not settlements:
            logger.info(f"[PlayLoop] No settlements for {provider_id} — {len(pending_bets)} still open")
            return

        # Wait for user to confirm the settlement breakdown
        logger.info(f"[PlayLoop] {len(settlements)} settlements for {provider_id} — waiting for confirm")
        self._confirmed_settlements = None
        self._settle_confirm_event.clear()
        await self._settle_confirm_event.wait()

        # Use only user-confirmed settlements if provided, otherwise all
        to_record = settlements
        if self._confirmed_settlements is not None:
            confirmed_ids = {s["bet_id"] for s in self._confirmed_settlements}
            to_record = [s for s in settlements if s["bet_id"] in confirmed_ids]

        if not to_record:
            logger.info(f"[PlayLoop] All settlements rejected for {provider_id}")
            self._broadcaster.publish("settlements_confirmed", {"provider_id": provider_id, "settlements": []})
            return

        # Record confirmed settlements to server
        await self._record_settlements(provider_id, to_record)
        self._broadcaster.publish(
            "settlements_confirmed",
            {
                "provider_id": provider_id,
                "settlements": to_record,
            },
        )

        # Sync balance after settlements
        try:
            balance = await workflow.sync_balance(page)
            await self._post_balance(provider_id, balance)
        except Exception:
            logger.warning(f"[PlayLoop] balance sync failed for {provider_id}")

    async def _reconcile_open_bets(self, provider_id: str, workflow, page) -> int:
        """Fetch open bets from provider and record any missing from our DB.

        Returns the number of newly recorded bets.
        """
        if not hasattr(workflow, "fetch_positions"):
            return 0

        try:
            positions = await workflow.fetch_positions(page)
        except Exception:
            logger.warning(f"[PlayLoop] fetch_positions failed for {provider_id}")
            return 0

        if not positions:
            return 0

        # Get our pending bets to find what's missing
        pending = await self._fetch_pending(provider_id)
        # Also check cluster siblings
        cluster = _PROVIDER_TO_CLUSTER.get(provider_id)
        if cluster:
            for sibling in _CLUSTER_MEMBERS.get(cluster, []):
                if sibling != provider_id:
                    sib_pending = await self._fetch_pending(sibling)
                    pending.extend(sib_pending)

        # Match by odds+stake (provider doesn't give us our event_id)
        known_keys = {(round(b["odds"], 2), round(b["stake"], 1)) for b in pending}

        new_count = 0
        for pos in positions:
            key = (round(pos.odds, 2), round(pos.stake, 1))
            if key not in known_keys:
                logger.info(
                    f"[PlayLoop] Unrecognized open bet on {provider_id}: "
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
                known_keys.discard(key)  # consume match

        if new_count:
            logger.info(f"[PlayLoop] {new_count} unrecognized open bets on {provider_id}")
        return new_count

    async def _fetch_pending(self, provider_id: str) -> list[dict]:
        """Fetch pending bets for a specific provider from server."""
        url = f"{self._proxy_url}/api/opportunities/play/pending-bets"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers={_AUTH_HEADER: _AUTH_VALUE})
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            logger.exception("[PlayLoop] failed to fetch pending bets")
            return []

        # API returns {providers: [{provider_id, bets: [...]}, ...]}
        for prov in data.get("providers", []):
            if prov.get("provider_id") == provider_id:
                return prov.get("bets", [])
        return []

    async def _fetch_placed_today(self, provider_id: str) -> None:
        """Fetch placed-today count from the batch endpoint."""
        url = f"{self._proxy_url}/api/opportunities/play/batch"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json={}, headers={_AUTH_HEADER: _AUTH_VALUE})
                resp.raise_for_status()
                data = resp.json()
            placed = data.get("placed_today", {})
            self._placed_today.update(placed)
        except Exception:
            logger.warning(f"[PlayLoop] failed to fetch placed_today for {provider_id}")

    async def _record_settlements(self, provider_id: str, settlements: list[dict]) -> None:
        """POST confirmed settlements to the server via settle-batch."""
        url = f"{self._proxy_url}/api/opportunities/play/settle-batch"
        batch = [
            {"bet_id": s["bet_id"], "result": s["result"]} for s in settlements if s.get("bet_id") and s.get("result")
        ]
        if not batch:
            logger.info(f"[PlayLoop] No valid settlements to record for {provider_id}")
            return
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json=batch, headers={_AUTH_HEADER: _AUTH_VALUE})
                resp.raise_for_status()
                data = resp.json()
            logger.info(
                f"[PlayLoop] Settlements recorded for {provider_id}: {data.get('settled', 0)}/{data.get('total', 0)}"
            )
        except Exception:
            logger.exception(f"[PlayLoop] Failed to record settlements for {provider_id}")

    async def _post_balance(self, provider_id: str, balance: float) -> None:
        """POST updated balance to the server."""
        url = f"{self._proxy_url}/api/bankroll/set/{provider_id}"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json={"balance": balance}, headers={_AUTH_HEADER: _AUTH_VALUE})
                resp.raise_for_status()
            logger.info(f"[PlayLoop] Balance posted for {provider_id}: {balance}")
        except Exception:
            logger.warning(f"[PlayLoop] Failed to post balance for {provider_id}")

    async def _record_bet(self, bet: dict[str, Any], result) -> None:
        """POST placed bet to the server DB via /api/bets."""
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
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    url,
                    json=payload,
                    headers={_AUTH_HEADER: _AUTH_VALUE},
                )
                resp.raise_for_status()
                data = resp.json()
                bet_id = data.get("bet_id", "?")
                logger.info(f"[PlayLoop] Recorded bet {bet_id} — {bet.get('event_id')}")
        except Exception:
            logger.exception(f"[PlayLoop] Failed to record bet for {bet.get('event_id')}")
