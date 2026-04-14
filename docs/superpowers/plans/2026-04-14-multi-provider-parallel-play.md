# Multi-Provider Parallel Play Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable opening and playing multiple providers simultaneously — each provider independently navigates to events, user places bets on whichever browser tab they want, interceptor records and advances that provider.

**Architecture:** Extract the per-provider logic from the monolithic `PlayLoop._run()` into a `ProviderRunner` class (one asyncio task per provider). Refactor `PlayLoop` into a `PlayCoordinator` that manages shared cluster queues and dispatches intercepted bets to the correct runner. Frontend switches from single-select to multi-select provider buttons.

**Tech Stack:** Python asyncio / FastAPI / React TypeScript

---

### Task 1: Extract ProviderRunner class from PlayLoop

**Files:**
- Create: `firevsports/mirror/provider_runner.py`
- Modify: `firevsports/mirror/play_loop.py`

This is the core refactor. The `ProviderRunner` encapsulates the per-provider state machine that currently lives inside `PlayLoop._run()`.

- [ ] **Step 1: Create `firevsports/mirror/provider_runner.py` with the ProviderRunner class**

This class takes a provider_id, shared references (browser, broadcaster, proxy_url), and a queue-pop callback. It runs as an independent asyncio task.

```python
"""ProviderRunner — independent per-provider play loop task.

Each runner owns its own state machine and processes bets from a shared
cluster queue. Multiple runners can run in parallel across different providers.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Callable, Awaitable

from .play_loop import (
    _bet_ns,
    _detect_settlements,
    DAILY_BET_CAP,
    LOGIN_POLL_INTERVAL,
    LOGIN_TIMEOUT,
    UNCAPPED_PROVIDERS,
    STATE_IDLE,
    STATE_PROVIDER_OPENING,
    STATE_LOGIN_WAITING,
    STATE_SETTLING,
    STATE_NAVIGATING,
    STATE_READY,
    STATE_PLACING,
)
from .workflows import get_workflow, PlacementResult

if TYPE_CHECKING:
    from .browser import MirrorBrowser
    from .sse import MirrorBroadcaster

logger = logging.getLogger(__name__)


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
        self._settle_confirm_event = asyncio.Event()
        self._intercepted_body: dict | None = None
        self._intercepted_request_body: dict | None = None
        self._confirmed_settlements: list[dict] | None = None

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
        self._settle_confirm_event.set()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def skip(self) -> None:
        self._skip_event.set()

    def on_bet_intercepted(self, body: dict, request_body: dict | None = None) -> None:
        if self.state != STATE_READY:
            return
        logger.info(f"[Runner:{self.provider_id}] Bet intercepted — auto-recording")
        self._intercepted_body = body
        self._intercepted_request_body = request_body
        self._bet_intercepted_event.set()

    def confirm_settlements(self, confirmed: list[dict] | None = None) -> None:
        self._confirmed_settlements = confirmed
        self._settle_confirm_event.set()

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
            for attempt in range(10):
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

            # 3. Settle pending
            await self._settle_pending(pid, workflow, page)

            # 4. Check daily cap
            if pid not in UNCAPPED_PROVIDERS:
                await self._fetch_placed_today(pid)
                placed = self._placed_today.get(pid, 0)
                if placed >= DAILY_BET_CAP:
                    logger.info(f"[Runner:{pid}] At daily cap ({placed}/{DAILY_BET_CAP})")
                    self._broadcaster.publish("provider_complete", {"provider_id": pid, "reason": f"daily cap ({placed}/{DAILY_BET_CAP})"})
                    return

            # 5. Process bets from shared queue
            while True:
                # Check cap before each bet
                if pid not in UNCAPPED_PROVIDERS:
                    placed = self._placed_today.get(pid, 0)
                    if placed >= DAILY_BET_CAP:
                        self._broadcaster.publish("provider_complete", {"provider_id": pid, "reason": f"daily cap ({placed}/{DAILY_BET_CAP})"})
                        break

                bet = self._pop_bet()
                if bet is None:
                    break  # Queue empty

                # Skip blocked bets
                if self._is_blocked(bet):
                    continue

                self.stats["total"] += 1

                # Override bet's provider_id to this runner's provider
                bet["provider_id"] = pid

                # Navigate
                self.state = STATE_NAVIGATING
                self.current_bet = bet

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

                # Check if event closed
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
                    self._broadcaster.publish("bet_skipped", {"bet": bet, "reason": f"negative EV ({live_odds:.2f}, edge {live_edge:.1f}%)", "live_odds": live_odds, "live_edge": live_edge})
                    self.stats["skipped"] += 1
                    continue

                # Ready — wait for interceptor or skip
                self.state = STATE_READY
                self._bet_intercepted_event.clear()
                self._skip_event.clear()
                self._intercepted_body = None
                self._intercepted_request_body = None
                self._broadcaster.publish("bet_ready", {
                    "bet": bet,
                    "provider_id": pid,
                    "prep_ok": prep_result.status == "prepped",
                    "live_odds": live_odds,
                    "live_edge": live_edge,
                    "prep_reason": prep_result.reason,
                })

                done, _ = await asyncio.wait(
                    [
                        asyncio.ensure_future(self._bet_intercepted_event.wait()),
                        asyncio.ensure_future(self._skip_event.wait()),
                    ],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if self._bet_intercepted_event.is_set():
                    self.state = STATE_PLACING
                    try:
                        provider_bet_id = None
                        actual_odds = prep_result.actual_odds
                        actual_stake = prep_result.actual_stake
                        requested_stake = stake
                        if self._intercepted_body:
                            if hasattr(workflow, "parse_placement_status"):
                                pstatus = workflow.parse_placement_status(self._intercepted_body)
                                if not pstatus["success"]:
                                    err = pstatus.get("error", "unknown error")
                                    self._broadcaster.publish("bet_failed", {"bet": bet, "reason": err})
                                    self.stats["skipped"] += 1
                                    continue

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
                                self._broadcaster.publish("stake_limited", {
                                    "bet": bet, "provider_id": pid,
                                    "requested_stake": requested_stake, "actual_stake": actual_stake,
                                })

                        # Autonomous placement (Pinnacle)
                        _balance_synced = False
                        if not self._intercepted_body and getattr(workflow, "autonomous_placement", False):
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
                                continue
                            else:
                                self._broadcaster.publish("bet_failed", {"bet": bet, "reason": api_result.reason})
                                self.stats["skipped"] += 1
                                continue
                        else:
                            result = PlacementResult(
                                status="placed",
                                bet_id=provider_bet_id or 0,
                                actual_odds=actual_odds,
                                actual_stake=actual_stake,
                                reason="intercepted" if self._intercepted_body else "manual",
                            )

                        placed_count = self._placed_today.get(pid, 0) + 1
                        self._broadcaster.publish("bet_placed", {
                            "bet": bet, "status": result.status,
                            "actual_odds": result.actual_odds, "actual_stake": result.actual_stake,
                            "placed_today": placed_count, "daily_cap": DAILY_BET_CAP,
                        })
                        self.stats["placed"] += 1
                        self._placed_today[pid] = self._placed_today.get(pid, 0) + 1
                        await self._record_bet(bet, result)
                        self._block_event_market(bet)
                        if not _balance_synced:
                            cached_bal = self._browser.provider_data.get(pid, {}).get("balance")
                            if cached_bal is not None:
                                await self._post_balance(pid, cached_bal)
                    except Exception:
                        logger.exception(f"[Runner:{pid}] Recording failed")
                        self._broadcaster.publish("bet_error", {"bet": bet, "reason": "record_exception"})
                        self.stats["skipped"] += 1
                else:
                    # Skipped
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
    # Helper methods — moved from PlayLoop
    # ------------------------------------------------------------------

    async def _wait_for_login(self, workflow, page) -> bool:
        await asyncio.sleep(2)
        elapsed = 2.0
        while elapsed < LOGIN_TIMEOUT:
            if self._browser.is_logged_in(workflow.provider_id):
                bal = self._browser.get_balance(workflow.provider_id)
                self._broadcaster.publish("login_detected", {"provider_id": workflow.provider_id, "balance": bal})
                return True
            try:
                dom_result = await self._browser.check_login_dom(workflow.provider_id)
                if dom_result.get("logged_in"):
                    self._broadcaster.publish("login_detected", {"provider_id": workflow.provider_id, "balance": dom_result.get("balance")})
                    return True
            except Exception:
                pass
            await asyncio.sleep(LOGIN_POLL_INTERVAL)
            elapsed += LOGIN_POLL_INTERVAL
            self._broadcaster.publish("login_waiting", {"provider_id": workflow.provider_id, "elapsed": round(elapsed), "timeout": LOGIN_TIMEOUT})
        return False

    @staticmethod
    async def _is_event_closed(page) -> bool:
        try:
            await asyncio.sleep(1.5)
            text = await page.evaluate("""() => {
                const main = document.querySelector('main, [class*="content"], [class*="event"]') || document.body;
                return (main.innerText || '').substring(0, 3000).toLowerCase();
            }""")
            closed_phrases = ["avslutat", "avslutad", "event has ended", "event is over", "event closed", "market closed", "market suspended", "no longer available", "inte tillgänglig"]
            return any(phrase in text for phrase in closed_phrases)
        except Exception:
            return False

    async def _settle_pending(self, provider_id: str, workflow, page) -> None:
        """Settle pending bets — simplified from PlayLoop._settle_pending.

        For parallel play, settlements auto-confirm (no user wait) to avoid
        blocking other runners. The user can review settlements in the Pending tab.
        """
        self.state = STATE_SETTLING
        self._broadcaster.publish("settling_pending", {"provider_id": provider_id})

        await self._reconcile_open_bets(provider_id, workflow, page)

        pending_bets = await self._fetch_pending(provider_id)
        if not pending_bets:
            self._broadcaster.publish("settling_done", {"provider_id": provider_id, "pending_count": 0, "settlements": []})
            return

        # Check positions to see if any settled
        try:
            positions = await workflow.fetch_positions(page) if hasattr(workflow, "fetch_positions") else None
        except Exception:
            positions = None

        if positions is not None:
            if len(positions) >= len(pending_bets):
                self._broadcaster.publish("settling_done", {"provider_id": provider_id, "pending_count": len(pending_bets), "settlements": []})
                return

        # Sync history
        from . import stream_registry
        stream = stream_registry.get(provider_id)
        if stream and stream.is_history_fresh():
            raw_history = stream.get_history()
        else:
            try:
                raw_history = await workflow.sync_history(page)
            except Exception:
                self._broadcaster.publish("settling_done", {"provider_id": provider_id, "pending_count": len(pending_bets), "settlements": []})
                return

        history = [{"odds": e.odds, "stake": e.stake, "status": e.status, "payout": e.payout, "provider_bet_id": e.provider_bet_id, "event_name": e.event_name} for e in raw_history]
        settlements = _detect_settlements(pending_bets, history)

        self._broadcaster.publish("settling_done", {
            "provider_id": provider_id,
            "pending_count": len(pending_bets),
            "pending_bets": pending_bets,
            "settlements": settlements,
        })

        # Auto-confirm settlements for parallel play (no blocking wait)
        if settlements:
            confirmed = [{"bet_id": s["bet_id"], "result": s["result"], "payout": s.get("payout")} for s in settlements]
            await self._post_settlements(provider_id, confirmed)

    async def _reconcile_open_bets(self, provider_id, workflow, page) -> None:
        if not hasattr(workflow, "fetch_positions"):
            return
        try:
            positions = await workflow.fetch_positions(page)
            if positions:
                open_bets = [{"provider_bet_id": p.provider_bet_id, "event_name": p.event_name, "market": p.market, "outcome": p.outcome, "odds": p.odds, "stake": p.stake} for p in positions]
                import httpx
                async with httpx.AsyncClient() as client:
                    await client.post(f"{self._proxy_url}/api/bets/reconcile-open", json={"provider_id": provider_id, "open_bets": open_bets}, headers={"X-Nginx-Authenticated": "firevsports"}, timeout=10)
        except Exception:
            logger.debug(f"[Runner:{provider_id}] reconcile_open_bets failed")

    async def _fetch_pending(self, provider_id: str) -> list[dict]:
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self._proxy_url}/api/bets/pending?provider_id={provider_id}", headers={"X-Nginx-Authenticated": "firevsports"}, timeout=10)
                if resp.status_code == 200:
                    return resp.json().get("bets", [])
        except Exception:
            logger.debug(f"[Runner:{provider_id}] _fetch_pending failed")
        return []

    async def _fetch_placed_today(self, provider_id: str) -> None:
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self._proxy_url}/api/bets/placed-today?provider_id={provider_id}", headers={"X-Nginx-Authenticated": "firevsports"}, timeout=10)
                if resp.status_code == 200:
                    count = resp.json().get("count", 0)
                    self._placed_today[provider_id] = count
        except Exception:
            logger.debug(f"[Runner:{provider_id}] _fetch_placed_today failed")

    async def _record_bet(self, bet: dict, result: PlacementResult) -> None:
        try:
            import httpx
            payload = {
                "provider_id": bet.get("provider_id"),
                "event_id": bet.get("event_id"),
                "market": bet.get("market"),
                "outcome": bet.get("outcome"),
                "point": bet.get("point"),
                "odds": result.actual_odds or bet.get("odds"),
                "stake": result.actual_stake or bet.get("stake"),
                "fair_odds": bet.get("fair_odds"),
                "edge_pct": bet.get("edge_pct"),
                "provider_bet_id": str(result.bet_id) if result.bet_id else None,
                "home_team": bet.get("display_home"),
                "away_team": bet.get("display_away"),
                "sport": bet.get("sport"),
                "league": bet.get("league"),
            }
            async with httpx.AsyncClient() as client:
                await client.post(f"{self._proxy_url}/api/bets/record", json=payload, headers={"X-Nginx-Authenticated": "firevsports"}, timeout=10)
        except Exception:
            logger.exception(f"[Runner:{self.provider_id}] _record_bet failed")

    async def _post_balance(self, provider_id: str, balance: float) -> None:
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                await client.post(f"{self._proxy_url}/api/providers/{provider_id}/balance", json={"balance": balance}, headers={"X-Nginx-Authenticated": "firevsports"}, timeout=5)
        except Exception:
            pass

    async def _post_settlements(self, provider_id: str, confirmed: list[dict]) -> None:
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                await client.post(f"{self._proxy_url}/api/bets/settle", json={"provider_id": provider_id, "settlements": confirmed}, headers={"X-Nginx-Authenticated": "firevsports"}, timeout=10)
        except Exception:
            logger.debug(f"[Runner:{provider_id}] _post_settlements failed")
```

- [ ] **Step 2: Verify import**

```bash
cd firevsports && python -c "from mirror.provider_runner import ProviderRunner; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add firevsports/mirror/provider_runner.py
git commit -m "feat(play): extract ProviderRunner from PlayLoop

Independent per-provider state machine that runs as an asyncio task.
Handles login, settle, navigate, ready, place cycle for one provider.
Multiple runners can operate in parallel."
```

---

### Task 2: Refactor PlayLoop into PlayCoordinator

**Files:**
- Modify: `firevsports/mirror/play_loop.py`

Refactor `PlayLoop` to manage multiple `ProviderRunner` instances with shared cluster queues.

- [ ] **Step 1: Rewrite PlayLoop as coordinator**

Replace the class body of `PlayLoop` in `firevsports/mirror/play_loop.py`. Keep the module-level constants and `_bet_ns()` function unchanged. Replace everything from `class PlayLoop:` onward:

```python
class PlayLoop:
    """PlayCoordinator — manages multiple ProviderRunners with shared cluster queues.

    Replaces the old single-provider PlayLoop. Each selected provider gets its
    own ProviderRunner asyncio task. Bets are partitioned into per-cluster queues.
    """

    def __init__(self, browser: MirrorBrowser, broadcaster: MirrorBroadcaster, proxy_url: str):
        self._browser = browser
        self._broadcaster = broadcaster
        self._proxy_url = proxy_url.rstrip("/")

        # Shared state
        self.state: str = STATE_IDLE
        self._placed_today: dict[str, int] = {}
        self._blocked: set[tuple[str, str]] = set()

        # Per-cluster queues: cluster_name → list of bets
        self._cluster_queues: dict[str, list[dict]] = {}
        self._cluster_locks: dict[str, asyncio.Lock] = {}
        self._queue_total: int = 0

        # Active runners: provider_id → ProviderRunner
        self._runners: dict[str, ProviderRunner] = {}
        self._coordinator_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_batch(self, batch: list[dict], balances: dict[str, float], provider_ids: list[str] | None = None, start_provider: str | None = None) -> None:
        """Partition bets into per-cluster queues for the selected providers."""
        # Backward compat: single provider_id → list
        if provider_ids is None and start_provider:
            provider_ids = [start_provider]
        if not provider_ids:
            provider_ids = []

        # Determine which clusters are funded
        funded_clusters: set[str] = set()
        for pid, bal in balances.items():
            if bal > 0:
                cluster = _PROVIDER_TO_CLUSTER.get(pid)
                if cluster:
                    funded_clusters.add(cluster)
                funded_clusters.add(pid)

        def _is_funded(b: dict) -> bool:
            pid = b.get("provider_id", "")
            cluster = _PROVIDER_TO_CLUSTER.get(pid)
            return pid in funded_clusters or (cluster is not None and cluster in funded_clusters)

        filtered = [b for b in batch if _is_funded(b)]
        filtered.sort(key=lambda b: -b.get("edge_pct", 0.0))

        # Partition into cluster queues
        self._cluster_queues.clear()
        self._cluster_locks.clear()
        for bet in filtered:
            bet_pid = bet.get("provider_id", "")
            cluster = _PROVIDER_TO_CLUSTER.get(bet_pid, bet_pid)
            if cluster not in self._cluster_queues:
                self._cluster_queues[cluster] = []
                self._cluster_locks[cluster] = asyncio.Lock()
            self._cluster_queues[cluster].append(bet)

        self._queue_total = len(filtered)
        self._provider_ids = provider_ids
        self._blocked.clear()
        logger.info(f"[PlayCoordinator] Loaded {self._queue_total} bets into {len(self._cluster_queues)} cluster queues for providers {provider_ids}")

    def start(self) -> None:
        """Spawn a ProviderRunner for each selected provider."""
        if self._coordinator_task and not self._coordinator_task.done():
            logger.warning("[PlayCoordinator] Already running")
            return
        self._coordinator_task = asyncio.create_task(self._run_coordinator(), name="play_coordinator")

    async def _run_coordinator(self) -> None:
        """Spawn runners and wait for all to complete."""
        self.state = STATE_RUNNING
        self._runners.clear()

        from .provider_runner import ProviderRunner

        for pid in self._provider_ids:
            cluster = _PROVIDER_TO_CLUSTER.get(pid, pid)
            if cluster not in self._cluster_queues:
                self._cluster_queues[cluster] = []
            if cluster not in self._cluster_locks:
                self._cluster_locks[cluster] = asyncio.Lock()

            runner = ProviderRunner(
                provider_id=pid,
                browser=self._browser,
                broadcaster=self._broadcaster,
                proxy_url=self._proxy_url,
                pop_bet=self._make_pop_bet(cluster),
                block_event_market=self._block_event_market,
                is_blocked=self._is_blocked,
                placed_today=self._placed_today,
            )
            self._runners[pid] = runner
            runner.start()

        # Wait for all runners to finish
        tasks = [r._task for r in self._runners.values() if r._task]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # All done
        provider_stats = {pid: r.stats for pid, r in self._runners.items()}
        self._broadcaster.publish("play_complete", {"provider_stats": provider_stats})
        logger.info(f"[PlayCoordinator] All runners complete")
        self.state = STATE_IDLE

    def stop(self) -> None:
        """Stop all runners and the coordinator."""
        for runner in self._runners.values():
            runner.stop()
        if self._coordinator_task and not self._coordinator_task.done():
            self._coordinator_task.cancel()
        self._coordinator_task = None
        self._runners.clear()
        self.state = STATE_IDLE
        self._broadcaster.publish("play_stopped", {})

    def place(self, provider_id: str | None = None) -> None:
        """Signal place for a specific runner (or first ready runner)."""
        runner = self._find_runner(provider_id, state=STATE_READY)
        if runner:
            runner._bet_intercepted_event.set()

    def skip(self, provider_id: str | None = None) -> None:
        """Signal skip for a specific runner (or first ready runner)."""
        runner = self._find_runner(provider_id, state=STATE_READY)
        if runner:
            runner.skip()

    def on_bet_intercepted(self, provider_id: str, body: dict, request_body: dict | None = None) -> None:
        """Route intercepted bet to the correct runner."""
        runner = self._runners.get(provider_id)
        if runner:
            runner.on_bet_intercepted(body, request_body)

    def confirm_settlements(self, confirmed: list[dict] | None = None, provider_id: str | None = None) -> None:
        """Route settlement confirmation to the correct runner."""
        runner = self._find_runner(provider_id, state=STATE_SETTLING)
        if runner:
            runner.confirm_settlements(confirmed)

    def get_status(self) -> dict:
        remaining = sum(len(q) for q in self._cluster_queues.values())
        return {
            "state": self.state,
            "queue_remaining": remaining,
            "queue_total": self._queue_total,
            "providers": {pid: r.get_status() for pid, r in self._runners.items()},
        }

    # ------------------------------------------------------------------
    # Queue helpers
    # ------------------------------------------------------------------

    def _make_pop_bet(self, cluster: str) -> callable:
        """Return a pop function for a specific cluster queue."""
        lock = self._cluster_locks[cluster]
        queue = self._cluster_queues[cluster]

        def pop() -> dict | None:
            # asyncio is single-threaded, but lock is here for safety
            if not queue:
                return None
            return queue.pop(0)

        return pop

    def _block_event_market(self, bet: dict) -> None:
        """Block event+market across all cluster queues."""
        event_id = bet.get("event_id", "")
        market = bet.get("market", "")
        market_key = "moneyline" if market in ("1x2", "moneyline") else market
        block_key = (event_id, market_key)
        self._blocked.add(block_key)
        # Remove from all queues
        for cluster, queue in self._cluster_queues.items():
            before = len(queue)
            self._cluster_queues[cluster] = [
                b for b in queue
                if (b.get("event_id"), "moneyline" if b.get("market") in ("1x2", "moneyline") else b.get("market")) != block_key
            ]
            removed = before - len(self._cluster_queues[cluster])
            if removed:
                logger.info(f"[PlayCoordinator] Blocked {event_id} {market_key} — removed {removed} from {cluster}")

    def _is_blocked(self, bet: dict) -> bool:
        event_id = bet.get("event_id", "")
        market = bet.get("market", "")
        market_key = "moneyline" if market in ("1x2", "moneyline") else market
        return (event_id, market_key) in self._blocked

    def _find_runner(self, provider_id: str | None, state: str | None = None) -> ProviderRunner | None:
        """Find a runner by provider_id, or first runner in the given state."""
        if provider_id and provider_id in self._runners:
            return self._runners[provider_id]
        if state:
            for r in self._runners.values():
                if r.state == state:
                    return r
        return None
```

- [ ] **Step 2: Clean up old imports and remove dead code**

Remove the old `_run()`, `_wait_for_login()`, `_is_event_closed()`, `_skip_provider()`, `_block_event_market()`, `_settle_pending()`, `_reconcile_open_bets()`, `_fetch_pending()`, `_fetch_placed_today()`, `_record_bet()`, `_post_balance()` methods from the file — they now live in `ProviderRunner`.

Keep: module-level constants (`STATE_*`, `_CLUSTER_MEMBERS`, `_PROVIDER_TO_CLUSTER`, `DAILY_BET_CAP`, `LOGIN_TIMEOUT`, `LOGIN_POLL_INTERVAL`, `UNCAPPED_PROVIDERS`), the `_bet_ns()` function, and `_detect_settlements()`.

- [ ] **Step 3: Verify import**

```bash
cd firevsports && python -c "from mirror.play_loop import PlayLoop; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add firevsports/mirror/play_loop.py
git commit -m "refactor(play): convert PlayLoop to multi-provider coordinator

Manages multiple ProviderRunners with shared per-cluster queues.
Each provider runs as an independent asyncio task. Dedup across
cluster siblings when a bet is placed."
```

---

### Task 3: Update router endpoints for multi-provider

**Files:**
- Modify: `firevsports/mirror/router.py:51-54` (PlayStartRequest)
- Modify: `firevsports/mirror/router.py:374-413` (play endpoints)

- [ ] **Step 1: Update PlayStartRequest model**

Change:
```python
class PlayStartRequest(BaseModel):
    batch: list[dict[str, Any]]
    balances: dict[str, Any]
    provider_id: str | None = None  # which skin to start on
```

To:
```python
class PlayStartRequest(BaseModel):
    batch: list[dict[str, Any]]
    balances: dict[str, Any]
    provider_id: str | None = None  # backward compat: single provider
    provider_ids: list[str] | None = None  # multi-provider: list of providers to start
```

- [ ] **Step 2: Update play_start endpoint**

Change:
```python
    @router.post("/play/start")
    async def play_start(req: PlayStartRequest):
        """Load a batch of bets and start the play loop."""
        play_loop.load_batch(req.batch, req.balances, start_provider=req.provider_id)
        play_loop.start()
        return play_loop.get_status()
```

To:
```python
    @router.post("/play/start")
    async def play_start(req: PlayStartRequest):
        """Load a batch of bets and start the play loop."""
        pids = req.provider_ids or ([req.provider_id] if req.provider_id else [])
        play_loop.load_batch(req.batch, req.balances, provider_ids=pids)
        play_loop.start()
        return play_loop.get_status()
```

- [ ] **Step 3: Update skip/place endpoints to accept provider_id**

Change:
```python
    @router.post("/play/place")
    async def play_place():
        """Confirm placement of the current bet in the play loop."""
        play_loop.place()
        return play_loop.get_status()

    @router.post("/play/skip")
    async def play_skip():
        """Skip the current bet in the play loop."""
        play_loop.skip()
        return play_loop.get_status()
```

To:
```python
    @router.post("/play/place")
    async def play_place(body: dict[str, Any] | None = None):
        """Confirm placement of the current bet in the play loop."""
        pid = (body or {}).get("provider_id")
        play_loop.place(provider_id=pid)
        return play_loop.get_status()

    @router.post("/play/skip")
    async def play_skip(body: dict[str, Any] | None = None):
        """Skip the current bet in the play loop."""
        pid = (body or {}).get("provider_id")
        play_loop.skip(provider_id=pid)
        return play_loop.get_status()
```

- [ ] **Step 4: Commit**

```bash
git add firevsports/mirror/router.py
git commit -m "feat(router): multi-provider play endpoints

Accept provider_ids list in start, provider_id in skip/place.
Backward compatible with single provider_id."
```

---

### Task 4: Route intercepted bets to correct runner

**Files:**
- Modify: `firevsports/mirror/browser.py` (the `on_bet_intercepted` dispatch)

The browser's response interceptor already calls `play_loop.on_bet_intercepted(provider_id, body, request_body)`. The refactored PlayLoop coordinator routes this to the correct runner. No change needed in browser.py — the existing call signature is preserved.

- [ ] **Step 1: Verify the call chain**

Read `firevsports/mirror/browser.py` and confirm that `_on_response` calls `self._play_loop.on_bet_intercepted(provider_id, body, request_body)` — this matches the coordinator's new signature.

```bash
cd firevsports && grep -n "on_bet_intercepted" mirror/browser.py
```

Expected: a call like `self._play_loop.on_bet_intercepted(provider_id, ...)` — no change needed.

- [ ] **Step 2: Commit (no-op — document verification)**

No code changes needed. The coordinator's `on_bet_intercepted` routes to the correct `ProviderRunner` by provider_id.

---

### Task 5: Frontend — multi-select providers and status display

**Files:**
- Modify: `firevsports/frontend/src/pages/PlayPage.tsx`
- Modify: `firevsports/frontend/src/hooks/useApi.ts`

- [ ] **Step 1: Update useApi.ts for multi-provider start**

Change:
```typescript
  startPlayLoop: (batch: any[], balances: Record<string, number>, providerId?: string) =>
    apiFetch<any>('/mirror/play/start', { method: 'POST', body: JSON.stringify({ batch, balances, provider_id: providerId }) }),
```

To:
```typescript
  startPlayLoop: (batch: any[], balances: Record<string, number>, providerIds: string[]) =>
    apiFetch<any>('/mirror/play/start', { method: 'POST', body: JSON.stringify({ batch, balances, provider_ids: providerIds }) }),
```

Also update skip to accept provider_id:
```typescript
  skipCurrent: (providerId?: string) =>
    apiFetch<any>('/mirror/play/skip', { method: 'POST', body: JSON.stringify({ provider_id: providerId }) }),
```

- [ ] **Step 2: Change PlayPage state from single-select to multi-select**

Replace:
```typescript
  const [activeCluster, setActiveCluster] = useState<string | null>(null)
  const [activeSkin, setActiveSkin] = useState<string | null>(null)
```

With:
```typescript
  const [activeProviders, setActiveProviders] = useState<Set<string>>(new Set())
```

- [ ] **Step 3: Replace `startSkin` with `toggleProvider`**

Replace the `startSkin` function:
```typescript
  const toggleProvider = (pid: string) => {
    setActiveProviders(prev => {
      const next = new Set(prev)
      if (next.has(pid)) next.delete(pid)
      else next.add(pid)
      return next
    })
  }

  const startAll = async () => {
    if (activeProviders.size === 0) return
    if (loopRunning) {
      api.stopPlayLoop()
      setLoopRunning(false)
      setCurrentBetReady(null)
      setLoopStatus(null)
      return
    }
    // Open tabs for all selected providers
    try { await api.startMirror() } catch { /* */ }
    await Promise.all([...activeProviders].map(pid => api.openTab(pid).catch(() => {})))
    // Collect bets from all selected clusters
    const selectedClusters = new Set([...activeProviders].map(pid => providerToCluster[pid] || pid))
    const allBets = bets.filter(b => selectedClusters.has(b.cluster || b.provider_id))
    setLoopRunning(true)
    await api.startPlayLoop(allBets, providerBalances, [...activeProviders])
  }
```

- [ ] **Step 4: Update provider button rendering for multi-select**

In the cluster header skin tabs section (around line 414), change:
```typescript
const isSkinActive = activeSkin === pid
```
To:
```typescript
const isSkinActive = activeProviders.has(pid)
```

Change the onClick handler:
```typescript
onClick={() => !disabled && startSkin(pid, clusterId)}
```
To:
```typescript
onClick={() => !disabled && toggleProvider(pid)}
```

- [ ] **Step 5: Add Start/Stop button and per-provider status rows**

Add after the cluster list (before the bet table), a control bar:
```tsx
      {/* Control bar */}
      {activeProviders.size > 0 && (
        <div className="flex items-center gap-2 px-3 py-1.5 border-b border-zinc-800 bg-zinc-900/80">
          <button
            onClick={startAll}
            className={`px-3 py-1 text-xs font-semibold rounded ${
              loopRunning
                ? 'bg-red-700/50 text-red-300 hover:bg-red-700/70'
                : 'bg-green-700/50 text-green-300 hover:bg-green-700/70'
            }`}
          >
            {loopRunning ? 'Stop' : `Start ${activeProviders.size} providers`}
          </button>
          <span className="text-[10px] text-zinc-500">{[...activeProviders].join(', ')}</span>
        </div>
      )}

      {/* Per-provider status rows */}
      {loopRunning && loopProviderStatus && (
        <div className="border-b border-zinc-800">
          {Object.entries(loopProviderStatus).map(([pid, status]: [string, any]) => (
            <div key={pid} className="flex items-center gap-2 px-3 py-1 border-b border-zinc-800/50 bg-zinc-900/30">
              <span className="text-[10px] font-semibold text-amber-400 uppercase w-20">{pid}</span>
              <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                status.state === 'ready' ? 'bg-green-900/40 text-green-400' :
                status.state === 'navigating' ? 'bg-blue-900/40 text-blue-400' :
                status.state === 'placing' ? 'bg-amber-900/40 text-amber-400' :
                status.state === 'settling' ? 'bg-purple-900/40 text-purple-400' :
                'bg-zinc-800 text-zinc-500'
              }`}>{status.state}</span>
              {status.current_bet && (
                <span className="text-[10px] text-zinc-300 truncate">
                  {status.current_bet.display_home} v {status.current_bet.display_away} — {status.current_bet.outcome} @ {status.current_bet.odds?.toFixed(2)}
                </span>
              )}
              {status.state === 'ready' && (
                <button onClick={() => api.skipCurrent(pid)} className="text-[10px] text-zinc-500 hover:text-zinc-300 ml-auto">skip</button>
              )}
            </div>
          ))}
        </div>
      )}
```

- [ ] **Step 6: Add provider status state from SSE**

Add state variable:
```typescript
const [loopProviderStatus, setLoopProviderStatus] = useState<Record<string, any> | null>(null)
```

In the SSE handler, update provider status from play_status polling or SSE events. Add to the `useEffect` for SSE events:
```typescript
    if (type === 'provider_opening' || type === 'login_waiting' || type === 'login_detected' ||
        type === 'settling_pending' || type === 'settling_done' ||
        type === 'bet_ready' || type === 'bet_placed' || type === 'bet_skipped' || type === 'bet_failed') {
      // Update per-provider status from individual events
      const pid = data.provider_id || data.bet?.provider_id
      if (pid) {
        setLoopProviderStatus(prev => ({
          ...prev,
          [pid]: {
            state: type === 'bet_ready' ? 'ready' :
                   type === 'bet_placed' || type === 'bet_skipped' || type === 'bet_failed' ? 'navigating' :
                   type.includes('login') ? 'login_waiting' :
                   type.includes('settl') ? 'settling' : 'opening',
            current_bet: data.bet || null,
          }
        }))
      }
    }
```

Update the play_complete/play_stopped handler:
```typescript
    if (type === 'play_complete' || type === 'play_stopped') {
      setLoopRunning(false)
      setCurrentBetReady(null)
      setActiveProviders(new Set())
      setLoopProviderStatus(null)
      setLoopStatus(null)
    }
```

And update provider_complete to remove that provider from status:
```typescript
    if (type === 'provider_complete') {
      setLoopProviderStatus(prev => {
        if (!prev) return prev
        const next = { ...prev }
        delete next[data.provider_id]
        return Object.keys(next).length > 0 ? next : null
      })
    }
```

- [ ] **Step 7: Verify frontend builds**

```bash
cd firevsports/frontend && npm run build 2>&1 | tail -5
```

- [ ] **Step 8: Commit**

```bash
git add firevsports/frontend/src/pages/PlayPage.tsx firevsports/frontend/src/hooks/useApi.ts
git commit -m "feat(ui): multi-provider selection and parallel status display

Toggle multiple providers on/off, Start/Stop button launches all.
Per-provider status rows show state + current bet for each runner."
```

---

### Task 6: Integration test — verify end-to-end

**Files:** None (manual verification)

- [ ] **Step 1: Start FirevSports**

```bash
cd firevsports && python launch.py
```

- [ ] **Step 2: Verify multi-select works**

1. Open the Play tab in the browser
2. Click multiple provider buttons — they should toggle on/off (amber highlight)
3. Click "Start N providers"
4. Verify SSE events show per-provider status rows
5. Verify each provider's browser tab navigates to events independently
6. Place a bet on one tab → verify interceptor records it and the provider advances
7. Click Stop → all runners stop

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "fix(play): integration fixes for multi-provider parallel play"
```
