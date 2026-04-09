# FirevSports Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the Play and Pending pages to the Playwright mirror browser with two parallel automation loops — auto-navigate/place bets (Play) and auto-sync settlements (Pending), recording everything to the server DB.

**Architecture:** Two async loops (PlayLoop, PendingLoop) share a single MirrorBrowser. A local SSE broadcaster pushes state to the frontend. Frontend listens via `useMirrorStream` hook and shows controls (Start/Stop, Place/Skip, Confirm). All DB writes go through the server API proxy.

**Tech Stack:** Python asyncio / FastAPI SSE / Playwright workflows | React EventSource hooks

**Spec:** `docs/superpowers/specs/2026-04-09-firevsports-automation-design.md`

---

## File Structure

### New files
- `firevsports/mirror/sse.py` — Local SSE broadcaster (mirrors `backend/src/pipeline/broadcast.py`)
- `firevsports/mirror/play_loop.py` — PlayLoop class: async state machine driving the betting flow
- `firevsports/mirror/pending_loop.py` — PendingLoop class: async loop syncing history + detecting settlements
- `firevsports/frontend/src/hooks/useMirrorStream.ts` — SSE hook for `/mirror/stream`
- `firevsports/tests/test_play_loop.py`
- `firevsports/tests/test_pending_loop.py`

### Modified files
- `firevsports/mirror/router.py` — add play/pending/stream endpoints
- `firevsports/server.py` — wire SSE broadcaster + loops into app lifecycle
- `firevsports/frontend/src/hooks/useApi.ts` — add play/pending control methods
- `firevsports/frontend/src/pages/PlayPage.tsx` — add automation UI (Start/Stop, Place/Skip, status)
- `firevsports/frontend/src/pages/PendingPage.tsx` — add Sync All, Confirm, status

---

## Task 1: SSE Broadcaster

**Files:**
- Create: `firevsports/mirror/sse.py`

- [ ] **Step 1: Create the broadcaster**

```python
# firevsports/mirror/sse.py
"""Local SSE broadcaster for mirror events."""
import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class MirrorBroadcaster:
    """Fan-out broadcaster: mirror loops publish, frontend SSE consumes."""

    def __init__(self):
        self._clients: dict[int, asyncio.Queue] = {}
        self._counter = 0

    def subscribe(self) -> tuple[int, asyncio.Queue]:
        self._counter += 1
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._clients[self._counter] = q
        return self._counter, q

    def unsubscribe(self, client_id: int) -> None:
        self._clients.pop(client_id, None)

    def publish(self, event_type: str, data: dict[str, Any]) -> None:
        message = {"event": event_type, "data": data}
        dead = []
        for cid, q in self._clients.items():
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                dead.append(cid)
        for cid in dead:
            self._clients.pop(cid, None)


mirror_broadcaster = MirrorBroadcaster()
```

- [ ] **Step 2: Commit**

```bash
git add firevsports/mirror/sse.py
git commit -m "feat(firevsports): add local SSE broadcaster for mirror events"
```

---

## Task 2: PlayLoop

**Files:**
- Create: `firevsports/mirror/play_loop.py`
- Test: `firevsports/tests/test_play_loop.py`

- [ ] **Step 1: Write test**

```python
# firevsports/tests/test_play_loop.py
"""Tests for PlayLoop state machine."""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from mirror.play_loop import PlayLoop


@pytest.fixture
def loop():
    browser = MagicMock()
    browser.running = True
    browser.context = MagicMock()
    broadcaster = MagicMock()
    broadcaster.publish = MagicMock()
    proxy_url = "http://localhost:18000"
    return PlayLoop(browser, broadcaster, proxy_url)


def test_initial_state(loop):
    assert loop.state == "idle"
    assert loop.current_bet is None


def test_start_sets_running(loop):
    batch = [{"provider_id": "betsson", "event_id": "e1", "market": "1x2",
              "outcome": "home", "odds": 2.0, "fair_odds": 1.9, "edge_pct": 5.0,
              "stake": 100, "expected_profit": 5, "display_home": "A", "display_away": "B",
              "tier": "soft", "cluster": "gecko_v2", "point": None}]
    balances = {"betsson": 500}
    loop.load_batch(batch, balances)
    assert len(loop._queue) == 1
    assert loop._queue[0]["provider_id"] == "betsson"


def test_unfunded_providers_excluded(loop):
    batch = [
        {"provider_id": "betsson", "event_id": "e1", "market": "1x2", "outcome": "home",
         "odds": 2.0, "fair_odds": 1.9, "edge_pct": 5.0, "stake": 100,
         "expected_profit": 5, "display_home": "A", "display_away": "B",
         "tier": "soft", "cluster": "gecko_v2", "point": None},
        {"provider_id": "comeon", "event_id": "e2", "market": "1x2", "outcome": "away",
         "odds": 3.0, "fair_odds": 2.5, "edge_pct": 10.0, "stake": 50,
         "expected_profit": 5, "display_home": "C", "display_away": "D",
         "tier": "soft", "cluster": "comeon_group", "point": None},
    ]
    balances = {"betsson": 500}  # comeon has no balance
    loop.load_batch(batch, balances)
    assert len(loop._queue) == 1
    assert loop._queue[0]["provider_id"] == "betsson"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd firevsports && python -m pytest tests/test_play_loop.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement PlayLoop**

```python
# firevsports/mirror/play_loop.py
"""PlayLoop — async state machine driving the automated betting flow."""
import asyncio
import logging
from typing import Any

import httpx

from .browser import MirrorBrowser
from .sse import MirrorBroadcaster
from .workflows import get_workflow

logger = logging.getLogger(__name__)


class PlayLoop:
    """Drives the Play automation: navigate → fill → wait for Place/Skip → record → advance."""

    def __init__(self, browser: MirrorBrowser, broadcaster: MirrorBroadcaster, proxy_url: str):
        self._browser = browser
        self._broadcaster = broadcaster
        self._proxy_url = proxy_url
        self._queue: list[dict] = []
        self._current_idx = 0
        self._current_provider: str | None = None
        self._task: asyncio.Task | None = None
        self._place_event = asyncio.Event()
        self._skip_event = asyncio.Event()
        self.state = "idle"
        self.current_bet: dict | None = None
        self.provider_stats: dict[str, dict] = {}  # {pid: {placed: N, skipped: N, total: N}}

    def load_batch(self, batch: list[dict], balances: dict[str, float]):
        """Load batch, filter to funded providers only, sort by edge desc."""
        funded = {pid for pid, bal in balances.items() if bal > 0}
        self._queue = [b for b in batch if b["edge_pct"] > 0 and b["provider_id"] in funded]
        self._queue.sort(key=lambda b: b["edge_pct"], reverse=True)
        # Group ordering: by provider, preserving edge sort within
        self._current_idx = 0
        # Init provider stats
        self.provider_stats = {}
        for b in self._queue:
            pid = b["provider_id"]
            if pid not in self.provider_stats:
                self.provider_stats[pid] = {"placed": 0, "skipped": 0, "total": 0}
            self.provider_stats[pid]["total"] += 1

    def start(self):
        """Start the play loop as an async task."""
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run())

    def stop(self):
        """Stop the play loop."""
        if self._task and not self._task.done():
            self._task.cancel()
        self.state = "idle"
        self.current_bet = None
        self._broadcaster.publish("play_stopped", {})

    def place(self):
        """User confirms current bet placement."""
        self._place_event.set()

    def skip(self):
        """User skips current bet."""
        self._skip_event.set()

    def get_status(self) -> dict:
        return {
            "state": self.state,
            "current_bet": self.current_bet,
            "queue_remaining": len(self._queue) - self._current_idx,
            "queue_total": len(self._queue),
            "provider_stats": self.provider_stats,
        }

    async def _run(self):
        """Main loop: iterate through queue, navigate, wait for action."""
        self.state = "running"
        self._broadcaster.publish("play_started", {})

        try:
            while self._current_idx < len(self._queue):
                bet = self._queue[self._current_idx]
                pid = bet["provider_id"]

                # Switch provider if needed
                if pid != self._current_provider:
                    self._current_provider = pid
                    self.state = "provider_opening"
                    self._broadcaster.publish("provider_activated", {"provider_id": pid, "status": "opening"})

                    # Find or open provider tab
                    workflow = get_workflow(pid)
                    context = self._browser.context
                    page = await workflow.find_tab(context)
                    if not page:
                        # Open provider site
                        domain = workflow.domain
                        page = await self._browser.open_tab(f"https://{domain}")
                        self._broadcaster.publish("provider_activated", {"provider_id": pid, "status": "tab_opened"})

                    # Wait for login (up to 120s)
                    self.state = "login_waiting"
                    logged_in = False
                    for _ in range(24):
                        try:
                            logged_in = await workflow.check_login(page)
                            if logged_in:
                                break
                        except Exception:
                            pass
                        await asyncio.sleep(5)

                    if not logged_in:
                        self._broadcaster.publish("provider_skipped", {"provider_id": pid, "reason": "login_timeout"})
                        # Skip all bets for this provider
                        while self._current_idx < len(self._queue) and self._queue[self._current_idx]["provider_id"] == pid:
                            self.provider_stats[pid]["skipped"] += 1
                            self._current_idx += 1
                        continue

                    self._broadcaster.publish("provider_activated", {"provider_id": pid, "status": "login_detected"})

                # Navigate to event
                self.state = "navigating"
                self.current_bet = bet
                workflow = get_workflow(pid)
                context = self._browser.context
                page = await workflow.find_tab(context)

                try:
                    navigated = await workflow.navigate_to_event(page, bet)
                    if not navigated:
                        raise Exception("Navigation returned False")
                    self._broadcaster.publish("bet_navigated", {"provider_id": pid, "event_id": bet["event_id"]})
                except Exception as e:
                    logger.warning(f"Navigation failed for {pid}/{bet['event_id']}: {e}")
                    self._broadcaster.publish("bet_skipped", {"bet_id": self._current_idx, "reason": "nav_failed"})
                    self.provider_stats[pid]["skipped"] += 1
                    self._current_idx += 1
                    continue

                # Ready — wait for Place or Skip
                self.state = "ready"
                self._place_event.clear()
                self._skip_event.clear()
                self._broadcaster.publish("bet_ready", {
                    "provider_id": pid,
                    "event_id": bet["event_id"],
                    "display_home": bet["display_home"],
                    "display_away": bet["display_away"],
                    "market": bet["market"],
                    "outcome": bet["outcome"],
                    "odds": bet["odds"],
                    "fair_odds": bet["fair_odds"],
                    "stake": bet["stake"],
                    "edge_pct": bet["edge_pct"],
                    "point": bet.get("point"),
                    "index": self._current_idx,
                    "total": len(self._queue),
                })

                # Wait for user action
                done, _ = await asyncio.wait(
                    [asyncio.create_task(self._place_event.wait()),
                     asyncio.create_task(self._skip_event.wait())],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if self._place_event.is_set():
                    # Place bet
                    self.state = "placing"
                    try:
                        result = await workflow.place_bet(page, bet, bet["stake"])
                        self._broadcaster.publish("bet_placed", {
                            "bet_id": self._current_idx,
                            "provider_id": pid,
                            "actual_odds": result.actual_odds,
                            "actual_stake": result.actual_stake,
                            "confirmation_id": getattr(result, "reason", None),
                            "status": result.status,
                        })
                        self.provider_stats[pid]["placed"] += 1

                        # Record to server DB
                        await self._record_bet(bet, result)

                    except Exception as e:
                        logger.exception(f"Placement failed for {pid}/{bet['event_id']}")
                        self._broadcaster.publish("bet_failed", {
                            "bet_id": self._current_idx,
                            "error": str(e),
                        })
                        self.provider_stats[pid]["skipped"] += 1

                elif self._skip_event.is_set():
                    self._broadcaster.publish("bet_skipped", {"bet_id": self._current_idx, "reason": "user_skip"})
                    self.provider_stats[pid]["skipped"] += 1

                self._current_idx += 1

                # Check if provider is done
                next_pid = self._queue[self._current_idx]["provider_id"] if self._current_idx < len(self._queue) else None
                if next_pid != pid:
                    self._broadcaster.publish("provider_complete", {
                        "provider_id": pid,
                        "placed": self.provider_stats[pid]["placed"],
                        "skipped": self.provider_stats[pid]["skipped"],
                    })

        except asyncio.CancelledError:
            self._broadcaster.publish("play_stopped", {})
        except Exception:
            logger.exception("PlayLoop crashed")
        finally:
            total_placed = sum(s["placed"] for s in self.provider_stats.values())
            total_skipped = sum(s["skipped"] for s in self.provider_stats.values())
            self._broadcaster.publish("play_complete", {
                "total_placed": total_placed,
                "total_skipped": total_skipped,
            })
            self.state = "idle"
            self.current_bet = None

    async def _record_bet(self, bet: dict, result: Any):
        """Record placed bet to server DB via API proxy."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    f"{self._proxy_url}/api/opportunities/play/settle-bet",
                    json={
                        "provider_id": bet["provider_id"],
                        "event_id": bet["event_id"],
                        "market": bet["market"],
                        "outcome": bet["outcome"],
                        "point": bet.get("point"),
                        "odds": result.actual_odds or bet["odds"],
                        "fair_odds": bet["fair_odds"],
                        "stake": result.actual_stake or bet["stake"],
                        "confirmation_id": getattr(result, "reason", None),
                        "bet_type": "value",
                    },
                    headers={"X-Nginx-Authenticated": "firevsports"},
                )
        except Exception:
            logger.exception(f"Failed to record bet to server DB")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd firevsports && python -m pytest tests/test_play_loop.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add firevsports/mirror/play_loop.py firevsports/tests/test_play_loop.py
git commit -m "feat(firevsports): add PlayLoop — automated betting state machine"
```

---

## Task 3: PendingLoop

**Files:**
- Create: `firevsports/mirror/pending_loop.py`
- Test: `firevsports/tests/test_pending_loop.py`

- [ ] **Step 1: Write test**

```python
# firevsports/tests/test_pending_loop.py
"""Tests for PendingLoop settlement sync."""
import pytest
from unittest.mock import MagicMock

from mirror.pending_loop import PendingLoop


@pytest.fixture
def loop():
    browser = MagicMock()
    browser.running = True
    browser.context = MagicMock()
    broadcaster = MagicMock()
    broadcaster.publish = MagicMock()
    proxy_url = "http://localhost:18000"
    return PendingLoop(browser, broadcaster, proxy_url)


def test_initial_state(loop):
    assert loop.running is False
    assert loop.provider_status == {}


def test_detect_settlements():
    """Comparing DB pending with history should detect settlements."""
    from mirror.pending_loop import _detect_settlements
    db_pending = [
        {"id": 1, "event_id": "e1", "odds": 2.0, "stake": 100, "result": "pending"},
        {"id": 2, "event_id": "e2", "odds": 3.0, "stake": 50, "result": "pending"},
    ]
    history = [
        {"provider_bet_id": "x1", "event_name": "e1", "odds": 2.0, "stake": 100, "status": "won", "payout": 200},
        {"provider_bet_id": "x2", "event_name": "e2", "odds": 3.0, "stake": 50, "status": "lost", "payout": 0},
    ]
    settlements = _detect_settlements(db_pending, history)
    assert len(settlements) == 2
    assert settlements[0]["bet_id"] == 1
    assert settlements[0]["result"] == "won"
    assert settlements[0]["payout"] == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd firevsports && python -m pytest tests/test_pending_loop.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement PendingLoop**

```python
# firevsports/mirror/pending_loop.py
"""PendingLoop — async loop syncing bet history and detecting settlements."""
import asyncio
import logging
from typing import Any

import httpx

from .browser import MirrorBrowser
from .sse import MirrorBroadcaster
from .workflows import get_workflow
from .workflows.base import HistoryEntry

logger = logging.getLogger(__name__)

SYNC_INTERVAL_S = 60


def _detect_settlements(db_pending: list[dict], history: list[dict]) -> list[dict]:
    """Compare DB pending bets with provider history to find settlements.

    Matches by odds + stake (fuzzy). Returns list of {bet_id, result, payout}.
    """
    settlements = []
    used_history = set()

    for bet in db_pending:
        if bet["result"] != "pending":
            continue
        for i, h in enumerate(history):
            if i in used_history:
                continue
            if h["status"] in ("pending", "cashout"):
                continue
            # Match by odds + stake (within 10%)
            odds_match = abs(h["odds"] - bet["odds"]) / max(bet["odds"], 0.01) < 0.10
            stake_match = abs(h["stake"] - bet["stake"]) / max(bet["stake"], 0.01) < 0.30
            if odds_match and stake_match:
                settlements.append({
                    "bet_id": bet["id"],
                    "result": h["status"],  # "won" | "lost" | "void"
                    "payout": h.get("payout", 0) or 0,
                })
                used_history.add(i)
                break

    return settlements


class PendingLoop:
    """Drives the Pending automation: sync history, detect settlements, confirm."""

    def __init__(self, browser: MirrorBrowser, broadcaster: MirrorBroadcaster, proxy_url: str):
        self._browser = browser
        self._broadcaster = broadcaster
        self._proxy_url = proxy_url
        self._task: asyncio.Task | None = None
        self._confirm_events: dict[str, asyncio.Event] = {}  # pid → Event
        self.running = False
        self.provider_status: dict[str, dict] = {}  # {pid: {last_sync, pending, settlements}}

    def start(self):
        if self._task and not self._task.done():
            return
        self.running = True
        self._task = asyncio.create_task(self._run())

    def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
        self.running = False
        self._broadcaster.publish("pending_stopped", {})

    def confirm(self, provider_id: str):
        """User confirms settlements for a provider."""
        if provider_id in self._confirm_events:
            self._confirm_events[provider_id].set()

    def get_status(self) -> dict:
        return {
            "running": self.running,
            "providers": self.provider_status,
        }

    async def _run(self):
        self._broadcaster.publish("pending_started", {})
        try:
            while self.running:
                await self._sync_all()
                await asyncio.sleep(SYNC_INTERVAL_S)
        except asyncio.CancelledError:
            pass
        finally:
            self.running = False
            self._broadcaster.publish("pending_stopped", {})

    async def _sync_all(self):
        """Sync all providers with pending bets."""
        # Fetch pending bets from server
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self._proxy_url}/api/opportunities/play/pending-bets",
                    headers={"X-Nginx-Authenticated": "firevsports"},
                )
                data = resp.json()
        except Exception:
            logger.exception("Failed to fetch pending bets")
            return

        providers = data.get("providers", [])

        for prov in providers:
            pid = prov["provider_id"]
            bets = prov.get("bets", [])
            if not bets:
                continue

            try:
                await self._sync_provider(pid, bets)
            except Exception:
                logger.exception(f"Failed to sync {pid}")

    async def _sync_provider(self, pid: str, db_bets: list[dict]):
        """Sync a single provider: open history, detect settlements."""
        workflow = get_workflow(pid)
        context = self._browser.context
        page = await workflow.find_tab(context)

        if not page:
            # Try to open provider site
            page = await self._browser.open_tab(f"https://{workflow.domain}")

        # Check login
        try:
            logged_in = await workflow.check_login(page)
            if not logged_in:
                self.provider_status[pid] = {"last_sync": None, "pending": len(db_bets), "settlements": []}
                return
        except Exception:
            return

        # Sync history
        try:
            history_entries = await workflow.sync_history(page)
            history = [
                {"provider_bet_id": h.provider_bet_id, "event_name": h.event_name,
                 "market": h.market, "outcome": h.outcome, "odds": h.odds,
                 "stake": h.stake, "status": h.status, "payout": h.payout}
                for h in history_entries
            ]
        except Exception:
            logger.exception(f"sync_history failed for {pid}")
            history = []

        self._broadcaster.publish("history_synced", {"provider_id": pid, "total_bets": len(history)})

        # Detect settlements
        settlements = _detect_settlements(db_bets, history)

        if settlements:
            self._broadcaster.publish("settlements_detected", {"provider_id": pid, "settlements": settlements})
            self.provider_status[pid] = {
                "last_sync": "now",
                "pending": len(db_bets),
                "settlements": settlements,
            }

            # Wait for user confirm (with 300s timeout)
            self._confirm_events[pid] = asyncio.Event()
            try:
                await asyncio.wait_for(self._confirm_events[pid].wait(), timeout=300)
                await self._record_settlements(pid, settlements)
                self._broadcaster.publish("settlements_confirmed", {"provider_id": pid, "count": len(settlements)})
            except asyncio.TimeoutError:
                logger.info(f"Settlement confirm timeout for {pid}")
            finally:
                self._confirm_events.pop(pid, None)
        else:
            self.provider_status[pid] = {"last_sync": "now", "pending": len(db_bets), "settlements": []}

        # Sync balance
        try:
            balance = await workflow.sync_balance(page)
            if balance is not None:
                self._broadcaster.publish("balance_updated", {"provider_id": pid, "amount": balance, "currency": "SEK"})
                # Update server DB
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.post(
                        f"{self._proxy_url}/api/bankroll/set/{pid}",
                        json={"balance": balance},
                        headers={"X-Nginx-Authenticated": "firevsports"},
                    )
        except Exception:
            logger.exception(f"Balance sync failed for {pid}")

    async def _record_settlements(self, pid: str, settlements: list[dict]):
        """Record confirmed settlements to server DB."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    f"{self._proxy_url}/api/opportunities/play/settle-confirm",
                    json={"provider_id": pid, "settlements": settlements},
                    headers={"X-Nginx-Authenticated": "firevsports"},
                )
        except Exception:
            logger.exception(f"Failed to record settlements for {pid}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd firevsports && python -m pytest tests/test_pending_loop.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add firevsports/mirror/pending_loop.py firevsports/tests/test_pending_loop.py
git commit -m "feat(firevsports): add PendingLoop — settlement sync + detection"
```

---

## Task 4: Wire Loops into Router + Server

**Files:**
- Modify: `firevsports/mirror/router.py`
- Modify: `firevsports/server.py`

- [ ] **Step 1: Add play/pending/stream endpoints to router.py**

Add to `create_mirror_router()` function in `firevsports/mirror/router.py`:

```python
# Add imports at top:
import asyncio
import json
from starlette.requests import Request
from sse_starlette.sse import EventSourceResponse
from .play_loop import PlayLoop
from .pending_loop import PendingLoop
from .sse import MirrorBroadcaster

# Change function signature:
def create_mirror_router(browser: MirrorBrowser, broadcaster: MirrorBroadcaster, proxy_url: str) -> APIRouter:
    play_loop = PlayLoop(browser, broadcaster, proxy_url)
    pending_loop = PendingLoop(browser, broadcaster, proxy_url)

    # ... existing endpoints stay ...

    # --- Play control ---

    @router.post("/play/start")
    async def play_start(request: Request):
        body = await request.json()
        batch = body.get("batch", [])
        balances = body.get("balances", {})
        play_loop.load_batch(batch, balances)
        play_loop.start()
        return {"status": "started", "queue_size": len(play_loop._queue)}

    @router.post("/play/place")
    async def play_place():
        play_loop.place()
        return {"status": "placed"}

    @router.post("/play/skip")
    async def play_skip():
        play_loop.skip()
        return {"status": "skipped"}

    @router.post("/play/stop")
    async def play_stop():
        play_loop.stop()
        return {"status": "stopped"}

    @router.get("/play/status")
    async def play_status():
        return play_loop.get_status()

    # --- Pending control ---

    @router.post("/pending/start")
    async def pending_start():
        pending_loop.start()
        return {"status": "started"}

    @router.post("/pending/confirm")
    async def pending_confirm(request: Request):
        body = await request.json()
        pid = body.get("provider_id")
        if pid:
            pending_loop.confirm(pid)
        return {"status": "confirmed", "provider_id": pid}

    @router.post("/pending/stop")
    async def pending_stop():
        pending_loop.stop()
        return {"status": "stopped"}

    @router.get("/pending/status")
    async def pending_status():
        return pending_loop.get_status()

    # --- SSE stream ---

    @router.get("/stream")
    async def mirror_stream(request: Request):
        client_id, queue = broadcaster.subscribe()
        async def generator():
            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(queue.get(), timeout=10.0)
                        yield {"event": msg["event"], "data": json.dumps(msg["data"])}
                    except asyncio.TimeoutError:
                        yield {"event": "heartbeat", "data": ""}
            except asyncio.CancelledError:
                pass
            finally:
                broadcaster.unsubscribe(client_id)
        return EventSourceResponse(generator(), ping=15)

    return router
```

- [ ] **Step 2: Update server.py to pass broadcaster and proxy_url**

In `firevsports/server.py`, update to pass the broadcaster and tunnel URL to the router:

```python
# Add import:
from .mirror.sse import mirror_broadcaster

# Change router creation:
app.include_router(create_mirror_router(browser, mirror_broadcaster, TUNNEL_URL))
```

Also update the `create_mirror_router` import if needed.

- [ ] **Step 3: Verify it loads**

Run: `cd firevsports && python -c "from server import app; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add firevsports/mirror/router.py firevsports/server.py
git commit -m "feat(firevsports): wire play/pending/stream endpoints into router"
```

---

## Task 5: Frontend — useMirrorStream Hook

**Files:**
- Create: `firevsports/frontend/src/hooks/useMirrorStream.ts`

- [ ] **Step 1: Create the hook**

```typescript
// firevsports/frontend/src/hooks/useMirrorStream.ts
import { useState, useEffect, useRef, useCallback } from 'react';

type MirrorEvent = {
  type: string;
  data: any;
};

type MirrorStreamState = {
  connected: boolean;
  lastEvent: MirrorEvent | null;
  events: MirrorEvent[];
};

export function useMirrorStream(): MirrorStreamState & { clearEvents: () => void } {
  const [connected, setConnected] = useState(false);
  const [lastEvent, setLastEvent] = useState<MirrorEvent | null>(null);
  const [events, setEvents] = useState<MirrorEvent[]>([]);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    const es = new EventSource('/mirror/stream');
    esRef.current = es;

    es.onopen = () => setConnected(true);
    es.onerror = () => {
      setConnected(false);
      // Auto-reconnect after 3s
      setTimeout(() => {
        if (esRef.current === es) {
          es.close();
          esRef.current = null;
        }
      }, 3000);
    };

    // Listen for all named events
    const eventTypes = [
      'play_started', 'provider_activated', 'provider_skipped', 'provider_complete',
      'bet_navigated', 'bet_ready', 'bet_placed', 'bet_skipped', 'bet_failed',
      'play_complete', 'play_stopped',
      'pending_started', 'history_synced', 'settlements_detected', 'settlements_confirmed',
      'balance_updated', 'pending_stopped',
    ];

    for (const type of eventTypes) {
      es.addEventListener(type, (e: MessageEvent) => {
        const evt: MirrorEvent = { type, data: JSON.parse(e.data) };
        setLastEvent(evt);
        setEvents(prev => [...prev.slice(-99), evt]);
      });
    }

    return () => {
      es.close();
      esRef.current = null;
      setConnected(false);
    };
  }, []);

  const clearEvents = useCallback(() => setEvents([]), []);

  return { connected, lastEvent, events, clearEvents };
}
```

- [ ] **Step 2: Add control methods to useApi.ts**

Add to `firevsports/frontend/src/hooks/useApi.ts`:

```typescript
  // Play loop control
  startPlayLoop: (batch: any[], balances: Record<string, number>) =>
    apiFetch<any>('/mirror/play/start', { method: 'POST', body: JSON.stringify({ batch, balances }) }),
  placeCurrent: () => apiFetch<any>('/mirror/play/place', { method: 'POST' }),
  skipCurrent: () => apiFetch<any>('/mirror/play/skip', { method: 'POST' }),
  stopPlayLoop: () => apiFetch<any>('/mirror/play/stop', { method: 'POST' }),
  getPlayStatus: () => apiFetch<any>('/mirror/play/status'),
  // Pending loop control
  startPendingLoop: () => apiFetch<any>('/mirror/pending/start', { method: 'POST' }),
  confirmSettlement: (pid: string) =>
    apiFetch<any>('/mirror/pending/confirm', { method: 'POST', body: JSON.stringify({ provider_id: pid }) }),
  stopPendingLoop: () => apiFetch<any>('/mirror/pending/stop', { method: 'POST' }),
  getPendingStatus: () => apiFetch<any>('/mirror/pending/status'),
```

- [ ] **Step 3: Build and commit**

```bash
cd firevsports/frontend && npm run build
git add firevsports/frontend/src/hooks/
git commit -m "feat(firevsports): add useMirrorStream hook + play/pending API methods"
```

---

## Task 6: PlayPage — Automation UI

**Files:**
- Modify: `firevsports/frontend/src/pages/PlayPage.tsx`

- [ ] **Step 1: Add automation controls to PlayPage**

Add at the top of the component (after state declarations):

```typescript
import { useMirrorStream } from '../hooks/useMirrorStream'

// Inside PlayPage component, add:
const mirror = useMirrorStream()
const [loopRunning, setLoopRunning] = useState(false)
const [currentBetReady, setCurrentBetReady] = useState<any>(null)

// Handle mirror events
useEffect(() => {
  if (!mirror.lastEvent) return
  const { type, data } = mirror.lastEvent
  if (type === 'bet_ready') setCurrentBetReady(data)
  if (type === 'bet_placed' || type === 'bet_skipped' || type === 'bet_failed') setCurrentBetReady(null)
  if (type === 'play_complete' || type === 'play_stopped') {
    setLoopRunning(false)
    setCurrentBetReady(null)
  }
}, [mirror.lastEvent])

const handleStartLoop = async () => {
  setLoopRunning(true)
  await api.startPlayLoop(batch, providerBalances)
}
const handleStopLoop = () => { api.stopPlayLoop(); setLoopRunning(false) }
const handlePlace = () => api.placeCurrent()
const handleSkip = () => api.skipCurrent()
```

Add to the header bar (after the EV display):

```tsx
{/* Automation controls */}
<div className="ml-auto flex items-center gap-2">
  {mirror.connected && <span className="w-1.5 h-1.5 rounded-full bg-green-500" />}
  {!loopRunning ? (
    <button onClick={handleStartLoop} disabled={bets.length === 0}
      className="px-2 py-0.5 text-xs bg-green-700 hover:bg-green-600 disabled:bg-zinc-800 text-white rounded">
      Start
    </button>
  ) : (
    <button onClick={handleStopLoop}
      className="px-2 py-0.5 text-xs bg-red-700 hover:bg-red-600 text-white rounded">
      Stop
    </button>
  )}
</div>
```

Add Place/Skip bar (shown when bet is ready):

```tsx
{currentBetReady && (
  <div className="flex items-center gap-3 px-3 py-2 bg-amber-900/30 border-b border-amber-700/50">
    <span className="text-xs text-amber-400 font-medium">
      Ready: {currentBetReady.display_home} v {currentBetReady.display_away} — {currentBetReady.outcome} @ {currentBetReady.odds}
    </span>
    <span className="text-xs text-green-400">+{currentBetReady.edge_pct?.toFixed(1)}%</span>
    <div className="ml-auto flex gap-2">
      <button onClick={handlePlace}
        className="px-3 py-1 text-xs bg-green-700 hover:bg-green-600 text-white rounded font-semibold">
        Place
      </button>
      <button onClick={handleSkip}
        className="px-3 py-1 text-xs bg-zinc-700 hover:bg-zinc-600 text-zinc-300 rounded">
        Skip
      </button>
    </div>
  </div>
)}
```

Highlight the current bet row in the table by comparing `event_id + outcome`:

```tsx
// In the bet row className, add:
${currentBetReady?.event_id === b.event_id && currentBetReady?.outcome === b.outcome ? 'bg-amber-900/20 border-l-2 border-amber-500' : ''}
```

- [ ] **Step 2: Build and commit**

```bash
cd firevsports/frontend && npm run build
git add firevsports/frontend/src/pages/PlayPage.tsx
git commit -m "feat(firevsports): add Play automation UI — Start/Stop, Place/Skip, bet highlighting"
```

---

## Task 7: PendingPage — Sync UI

**Files:**
- Modify: `firevsports/frontend/src/pages/PendingPage.tsx`

- [ ] **Step 1: Add sync controls to PendingPage**

Add to PendingPage:

```typescript
import { useMirrorStream } from '../hooks/useMirrorStream'

// Inside component:
const mirror = useMirrorStream()
const [syncing, setSyncing] = useState(false)
const [detectedSettlements, setDetectedSettlements] = useState<Record<string, any[]>>({})

useEffect(() => {
  if (!mirror.lastEvent) return
  const { type, data } = mirror.lastEvent
  if (type === 'settlements_detected') {
    setDetectedSettlements(prev => ({ ...prev, [data.provider_id]: data.settlements }))
  }
  if (type === 'settlements_confirmed') {
    setDetectedSettlements(prev => {
      const next = { ...prev }
      delete next[data.provider_id]
      return next
    })
    // Refetch pending bets
    queryClient.invalidateQueries({ queryKey: ['pending-bets'] })
  }
  if (type === 'pending_stopped') setSyncing(false)
}, [mirror.lastEvent])

const handleSyncAll = async () => {
  setSyncing(true)
  await api.startPendingLoop()
}
const handleStopSync = () => { api.stopPendingLoop(); setSyncing(false) }
const handleConfirm = (pid: string) => api.confirmSettlement(pid)
```

Add to the header bar:

```tsx
<div className="flex items-center gap-3 px-3 py-2 border-b border-zinc-800">
  <span className="text-xs text-zinc-400">{totalBets} pending bets across {providers.length} providers</span>
  <div className="ml-auto flex items-center gap-2">
    {mirror.connected && <span className="w-1.5 h-1.5 rounded-full bg-green-500" />}
    {!syncing ? (
      <button onClick={handleSyncAll}
        className="px-2 py-0.5 text-xs bg-amber-600 hover:bg-amber-500 text-white rounded">
        Sync All
      </button>
    ) : (
      <button onClick={handleStopSync}
        className="px-2 py-0.5 text-xs bg-red-700 hover:bg-red-600 text-white rounded">
        Stop
      </button>
    )}
  </div>
</div>
```

Add settlement detection UI per provider (after the bet table):

```tsx
{detectedSettlements[p.provider_id] && (
  <div className="px-6 py-2 bg-amber-900/20 border-b border-amber-700/30">
    <div className="flex items-center gap-3">
      <span className="text-xs text-amber-400 font-medium">
        {detectedSettlements[p.provider_id].length} settlements detected
      </span>
      <button onClick={() => handleConfirm(p.provider_id)}
        className="px-2 py-0.5 text-xs bg-green-700 hover:bg-green-600 text-white rounded">
        Confirm
      </button>
    </div>
    {detectedSettlements[p.provider_id].map((s: any, i: number) => (
      <div key={i} className="flex gap-3 text-xs mt-1">
        <span className="text-zinc-400">Bet #{s.bet_id}</span>
        <span className={s.result === 'won' ? 'text-green-400' : s.result === 'lost' ? 'text-red-400' : 'text-zinc-400'}>
          {s.result}
        </span>
        <span className="text-zinc-300">{s.payout > 0 ? `+${s.payout.toFixed(0)} kr` : ''}</span>
      </div>
    ))}
  </div>
)}
```

- [ ] **Step 2: Build and commit**

```bash
cd firevsports/frontend && npm run build
git add firevsports/frontend/src/pages/PendingPage.tsx
git commit -m "feat(firevsports): add Pending sync UI — Sync All, settlement detection, Confirm"
```

---

## Task 8: Integration Test + Build

- [ ] **Step 1: Build frontend**

```bash
cd firevsports/frontend && npm run build
```

- [ ] **Step 2: Run all Python tests**

```bash
cd firevsports && python -m pytest tests/ -v
```

- [ ] **Step 3: Verify server loads with all wiring**

```bash
cd firevsports && python -c "from server import app; print([r.path for r in app.routes if hasattr(r, 'path')])"
```

Expected output should include: `/mirror/play/start`, `/mirror/play/place`, `/mirror/play/skip`, `/mirror/play/stop`, `/mirror/play/status`, `/mirror/pending/start`, `/mirror/pending/confirm`, `/mirror/pending/stop`, `/mirror/pending/status`, `/mirror/stream`

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat(firevsports): automated play + pending loops with SSE streaming"
```
